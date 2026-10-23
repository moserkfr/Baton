"""Move a process's tracked GPU tensors to host RAM and back, without breaking user references.

The trick: `t.data = other` swaps the storage behind a tensor while keeping the same Python object.
A model's parameters, an optimizer's state and the user's own variables all keep pointing at the same
tensor objects; only where their bytes live changes (GPU -> pinned host buffer -> GPU).

Rules that came out of testing on the laptop:
* Restore every tensor to its ORIGINAL device. Adam keeps its `step` counter on the CPU on purpose;
  moving it to the GPU breaks `optimizer.step()`. We only ever move tensors that were on a GPU.
* Allocating pinned (page-locked) host memory is slow, so the pinned buffers are allocated on the
  first park (or up front with `prealloc()`) and reused for every later park.
"""
from __future__ import annotations

import gc
import hashlib
import time
from dataclasses import dataclass
from typing import Iterator

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
        self._modules: list[tuple[str, torch.nn.Module]] = []
        self._optimizers: list[tuple[str, torch.optim.Optimizer]] = []
        self._tensors: list[tuple[str, torch.Tensor]] = []
        self._pinned: dict[str, torch.Tensor] = {}   # reused host buffers, keyed by tensor name
        self._home: dict[str, torch.device] = {}     # original device of each parked tensor
        self.parked = False

    # ------------------------------------------------------------ registration
    def track_module(self, module: torch.nn.Module, name: str | None = None) -> torch.nn.Module:
        self._modules.append((name or f"module{len(self._modules)}", module))
        return module

    def track_optimizer(self, opt: torch.optim.Optimizer, name: str | None = None) -> torch.optim.Optimizer:
        self._optimizers.append((name or f"optim{len(self._optimizers)}", opt))
        return opt

    def track_tensor(self, t: torch.Tensor, name: str | None = None) -> torch.Tensor:
        self._tensors.append((name or f"tensor{len(self._tensors)}", t))
        return t

    def iter_tensors(self) -> Iterator[tuple[str, torch.Tensor]]:
        """Every tracked tensor once, with a stable name: params, grads, buffers, optimizer state, raw."""
        seen: set[int] = set()

        def emit(name: str, t: torch.Tensor | None):
            if t is not None and torch.is_tensor(t) and id(t) not in seen:
                seen.add(id(t))
                yield name, t

        for mname, m in self._modules:
            for pname, p in m.named_parameters():
                yield from emit(f"{mname}.{pname}", p)
                yield from emit(f"{mname}.{pname}.grad", p.grad)
            for bname, b in m.named_buffers():
                yield from emit(f"{mname}.{bname}", b)
        for oname, opt in self._optimizers:
            for i, (param, state) in enumerate(opt.state.items()):
                for key, v in state.items():
                    yield from emit(f"{oname}.{i}.{key}", v)
        for tname, t in self._tensors:
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

    def restore(self) -> ParkStats:
        """Copy every parked tensor back to its original device."""
        if not self.parked:
            raise RuntimeError("not parked")
        t0 = time.perf_counter()
        moved, n = 0, 0
        for name, t in self.iter_tensors():
            home = self._home.get(name)
            if home is None:
                continue
            new = torch.empty(t.shape, dtype=t.dtype, device=home)
            new.copy_(t.detach(), non_blocking=self.pin_memory)
            t.data = new
            moved += t.numel() * t.element_size()
            n += 1
        torch.cuda.synchronize()
        self._home.clear()
        self.parked = False
        return ParkStats(time.perf_counter() - t0, moved, n, 0, torch.cuda.memory_reserved())

    # ------------------------------------------------------------ verification
    def checksum(self) -> str:
        """SHA-256 over every tracked tensor's bytes, in a stable order (location independent)."""
        h = hashlib.sha256()
        for name, t in self.iter_tensors():
            h.update(name.encode())
            h.update(t.detach().to("cpu").contiguous().reshape(-1).view(torch.uint8).numpy().tobytes())
        return h.hexdigest()[:16]

    def devices(self) -> dict[str, str]:
        return {name: str(t.device) for name, t in self.iter_tensors()}
