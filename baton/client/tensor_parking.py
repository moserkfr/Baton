"""Move a process's tracked GPU tensors to host RAM and back, without breaking user references.

The trick: `t.data = other` swaps the storage behind a tensor while keeping the same Python object.
A model's parameters, an optimizer's state and the user's own variables all keep pointing at the same
tensor objects; only where their bytes live changes (GPU -> pinned host buffer -> GPU).

Rules that came out of testing on the laptop:
* Restore every tensor to its ORIGINAL device. Adam keeps its `step` counter on the CPU on purpose;
  moving it to the GPU breaks `optimizer.step()`. We only ever move tensors that were on a GPU.
* Allocating pinned (page-locked) host memory is slow, so the pinned buffers are allocated on the
  first park (or up front with `prealloc()`) and reused for every later park.

Second level, for long absences: `spill(path)` writes the parked state to disk and frees the host RAM
too. Each tensor then points at a one-element placeholder expanded to its shape (no real memory, shape
unchanged); `restore()` reads the file straight back onto the GPU and deletes it.
"""
from __future__ import annotations

import gc
import hashlib
import os
import time
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import torch


def _clear_cublas_workspaces() -> None:
    clear = getattr(torch._C, "_cuda_clearCublasWorkspaces", None)  # private API; skip if it moves
    if clear is not None:
        clear()


@dataclass
class ParkStats:
    seconds: float
    bytes_moved: int
    tensors: int
    reserved_before: int
    reserved_after: int


class TensorParker:
    """Owns the list of tensors a student registered and parks/restores them as a unit."""

    def __init__(self, pin_memory: bool = True):
        self.pin_memory = pin_memory
        # entries are (name, object) or, for auto-discovered objects, (name, weakref): a notebook variable
        # the student deletes must not be kept alive by us
        self._modules: list[tuple[str, Any]] = []
        self._optimizers: list[tuple[str, Any]] = []
        self._tensors: list[tuple[str, Any]] = []
        self._ids: set[int] = set()                  # tracked objects, so nothing is tracked twice
        self._pinned: dict[str, torch.Tensor] = {}   # reused host buffers, keyed by tensor name
        self._home: dict[str, torch.device] = {}     # original device of each parked tensor
        self.parked = False
        self.spill_path: Path | None = None          # set while the parked state lives on disk

    # ------------------------------------------------------------ registration
    def _add(self, bucket: list, prefix: str, obj: Any, name: str | None, weak: bool) -> bool:
        if id(obj) in self._ids:
            return False
        self._ids.add(id(obj))
        ref = weakref.ref(obj, lambda _r, i=id(obj): self._ids.discard(i)) if weak else obj
        bucket.append((name or f"{prefix}{len(bucket)}", ref))
        return True

    def track_module(self, module: torch.nn.Module, name: str | None = None,
                     weak: bool = False) -> torch.nn.Module:
        self._add(self._modules, "module", module, name, weak)
        return module

    def track_optimizer(self, opt: torch.optim.Optimizer, name: str | None = None,
                        weak: bool = False) -> torch.optim.Optimizer:
        self._add(self._optimizers, "optim", opt, name, weak)
        return opt

    def track_tensor(self, t: torch.Tensor, name: str | None = None, weak: bool = False) -> torch.Tensor:
        self._add(self._tensors, "tensor", t, name, weak)
        return t

    def is_tracked(self, obj: Any) -> bool:
        return id(obj) in self._ids

    @staticmethod
    def _live(bucket: list) -> Iterator[tuple[str, Any]]:
        for name, ref in bucket:
            obj = ref() if isinstance(ref, weakref.ref) else ref
            if obj is not None:
                yield name, obj

    def iter_tensors(self) -> Iterator[tuple[str, torch.Tensor]]:
        """Every tracked tensor once, with a stable name: params, grads, buffers, optimizer state, raw."""
        seen: set[int] = set()

        def emit(name: str, t: torch.Tensor | None):
            if t is not None and torch.is_tensor(t) and id(t) not in seen:
                seen.add(id(t))
                yield name, t

        for mname, m in self._live(self._modules):
            for pname, p in m.named_parameters():
                yield from emit(f"{mname}.{pname}", p)
                yield from emit(f"{mname}.{pname}.grad", p.grad)
            for bname, b in m.named_buffers():
                yield from emit(f"{mname}.{bname}", b)
        for oname, opt in self._live(self._optimizers):
            for i, (param, state) in enumerate(opt.state.items()):
                for key, v in state.items():
                    yield from emit(f"{oname}.{i}.{key}", v)
        for tname, t in self._live(self._tensors):
            yield from emit(tname, t)

    def gpu_bytes(self) -> int:
        return sum(t.numel() * t.element_size() for _, t in self.iter_tensors() if t.is_cuda)

    # ------------------------------------------------------------ pinned buffer pool
    def _host_buffer(self, name: str, t: torch.Tensor) -> torch.Tensor:
        buf = self._pinned.get(name)
        if buf is None or buf.shape != t.shape or buf.dtype != t.dtype:
            buf = torch.empty(t.shape, dtype=t.dtype, device="cpu", pin_memory=self.pin_memory)
            self._pinned[name] = buf
        return buf

    def prealloc(self) -> float:
        """Allocate the pinned buffers now so the first park is as fast as later ones. Returns seconds."""
        t0 = time.perf_counter()
        for name, t in self.iter_tensors():
            if t.is_cuda:
                self._host_buffer(name, t)
        return time.perf_counter() - t0

    def pinned_bytes(self) -> int:
        return sum(b.numel() * b.element_size() for b in self._pinned.values())

    # ------------------------------------------------------------ park / restore
    def park(self) -> ParkStats:
        """Copy every GPU tensor into its host buffer, point the tensor at it, free the GPU memory."""
        if self.parked:
            raise RuntimeError("already parked")
        reserved_before = torch.cuda.memory_reserved()
        t0 = time.perf_counter()
        torch.cuda.synchronize()  # no GPU work in flight (the caller holds the step lock as well)
        for _, t in self._live(self._tensors):
            # A result like `loss` or `out` keeps its autograd graph, and the graph keeps the GPU inputs it
            # saved for backward. Drop the graph so that memory is freed too (no .backward() on a result
            # computed before the park; views can't be detached in place and are never tracked).
            if t.grad_fn is not None and t._base is None:
                t.detach_()
        moved, n = 0, 0
        pairs = []
        for name, t in self.iter_tensors():
            if not t.is_cuda:
                continue  # e.g. Adam's `step` stays where it is
            buf = self._host_buffer(name, t)
            buf.copy_(t.detach(), non_blocking=self.pin_memory)
            pairs.append((name, t, buf))
            moved += t.numel() * t.element_size()
            n += 1
        torch.cuda.synchronize()  # copies done before we drop the GPU storage
        for name, t, buf in pairs:
            self._home[name] = t.device
            t.data = buf
        del pairs
        gc.collect()
        _clear_cublas_workspaces()  # cuBLAS keeps a ~16 MiB workspace inside the caching allocator
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        self.parked = True
        return ParkStats(time.perf_counter() - t0, moved, n, reserved_before, torch.cuda.memory_reserved())

    def spill(self, path: str | Path) -> ParkStats:
        """Write the parked tensors to `path` and free their host RAM (second-level parking)."""
        if not self.parked or self.spill_path is not None:
            raise RuntimeError("spill needs a parked, not yet spilled session")
        t0 = time.perf_counter()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {name: t.detach() for name, t in self.iter_tensors() if name in self._home}
        tmp = path.with_suffix(".tmp")
        torch.save(state, tmp)
        os.replace(tmp, path)               # a crash mid-write never leaves a half file under the real name
        moved = sum(t.numel() * t.element_size() for t in state.values())
        del state
        for name, t in self.iter_tensors():
            if name in self._home:          # shape kept, storage is a single element
                t.data = torch.zeros(1, dtype=t.dtype).expand(t.shape)
        self._pinned.clear()
        gc.collect()
        self.spill_path = path
        return ParkStats(time.perf_counter() - t0, moved, len(self._home), 0, torch.cuda.memory_reserved())

    def _spilled_state(self) -> dict[str, torch.Tensor]:
        return torch.load(self.spill_path, map_location="cpu", weights_only=True)

    def restore(self) -> ParkStats:
        """Copy every parked tensor back to its original device (from RAM, or from disk if spilled)."""
        if not self.parked:
            raise RuntimeError("not parked")
        t0 = time.perf_counter()
        source = self._spilled_state() if self.spill_path is not None else None
        moved, n = 0, 0
        for name, t in self.iter_tensors():
            home = self._home.get(name)
            if home is None:
                continue
            src = source[name] if source is not None else t.detach()
            new = torch.empty(t.shape, dtype=t.dtype, device=home)
            new.copy_(src, non_blocking=self.pin_memory and source is None)
            t.data = new
            moved += t.numel() * t.element_size()
            n += 1
        torch.cuda.synchronize()
        self._home.clear()
        self.parked = False
        if self.spill_path is not None:
            self.spill_path.unlink(missing_ok=True)
            self.spill_path = None
        return ParkStats(time.perf_counter() - t0, moved, n, 0, torch.cuda.memory_reserved())

    def discard_spill(self) -> None:
        """Delete the spill file (the process is exiting without restoring)."""
        if self.spill_path is not None:
            self.spill_path.unlink(missing_ok=True)

    # ------------------------------------------------------------ verification
    def checksum(self) -> str:
        """SHA-256 over every tracked tensor's bytes, in a stable order (location independent)."""
        h = hashlib.sha256()
        spilled = self._spilled_state() if self.spill_path is not None else {}
        for name, t in self.iter_tensors():
            src = spilled.get(name, t).detach()
            h.update(name.encode())
            h.update(src.to("cpu").contiguous().reshape(-1).view(torch.uint8).numpy().tobytes())
        return h.hexdigest()[:16]

    def devices(self) -> dict[str, str]:
        return {name: str(t.device) for name, t in self.iter_tensors()}
