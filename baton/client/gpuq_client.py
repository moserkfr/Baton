"""gpuq_client: the small library a student's process imports to share a GPU politely.

    from baton.client.gpuq_client import GpuqClient
    gq = GpuqClient(user="alice")
    gq.connect()                          # register; waits for a slot; caps memory to the slot
    model = gq.track(MyNet().cuda())      # tell the parker what to move if we are parked
    opt = gq.track(torch.optim.Adam(model.parameters()))
    while True:
        with gq.step():                   # GPU work happens inside a step: never parked mid-step
            train_one_batch(model, opt)

What happens behind the scenes:
* A reader thread listens to the daemon. On `park` it takes the step lock (so it waits for any running
  step to finish), moves every tracked tensor into reused pinned host buffers and frees the GPU memory.
  The process keeps running; only its GPU memory moved.
* If the student calls `gq.step()` while parked, the client asks the daemon for a slot and blocks until
  the daemon sends `resume`; then every tensor is copied back to its original device and the step runs.
* A reporter thread sends `torch.cuda.memory_reserved()` every second, because NVML cannot report
  per-process memory under Windows' WDDM driver model.
* The PID sent on registration is `os.getpid()`: on Windows a venv's python.exe is a launcher, so the PID
  a parent gets from Popen belongs to the launcher, not to the process that owns the GPU memory.
* If the daemon disappears, the client fails open: steps keep running, nothing is parked.

Friendlier extras: `gq.autotrack()` finds models/optimizers/GPU tensors by itself (the Jupyter extension
does this after every cell), `gq.snooze()` postpones a warning, `gq.away(minutes=45)` parks now and
restores just before you're back, and long-parked sessions are spilled to disk when the daemon asks.
"""
from __future__ import annotations

import inspect
import os
import re
import socket
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

import torch

from .. import protocol
from .autotrack import discover
from .tensor_parking import TensorParker

DEFAULT_HOST, DEFAULT_PORT = "127.0.0.1", 7311


class GpuqClient:
    def __init__(self, user: str, slot_mem_gib: float | None = None, host: str = DEFAULT_HOST,
                 port: int = DEFAULT_PORT, name: str | None = None, report_interval: float = 1.0,
                 whitelist: bool = False, pin_memory: bool = True, verbose: bool = True,
                 on_message: Callable[[dict[str, Any]], None] | None = None):
        self.user, self.slot_mem_gib, self.name = user, slot_mem_gib, name or user
        self.host, self.port = host, port
        self.report_interval, self.whitelist, self.verbose = report_interval, whitelist, verbose
        self.on_message = on_message
        self.parker = TensorParker(pin_memory=pin_memory)
        self.session: str | None = None
        self.slot: int | None = None
        self.state = "new"            # new | queued | running | parking | parked | resuming | disconnected
        self.steps = 0
        self.last_step = time.time()
        self.in_step = False
        self.history: list[tuple[float, str, dict]] = []   # (time, event, details) for demos/tests
        self._gpu_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._granted = threading.Event()
        self._resumed = threading.Event()
        self._resume_requested = False
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._f = None
        self._auto_n = 0
        self.notifications: list[dict[str, Any]] = []

    # ------------------------------------------------------------ connection
    def connect(self, timeout: float | None = None) -> "GpuqClient":
        """Register with the daemon and block until this session holds a slot."""
        self._sock, self._f = protocol.connect(self.host, self.port)
        self._send({"type": "register", "user": self.user, "pid": os.getpid(), "name": self.name,
                    "slot_mem_gib": self.slot_mem_gib, "whitelist": self.whitelist})
        reply = protocol.recv(self._f)
        if not reply or reply.get("type") != "registered":
            raise ConnectionError(f"unexpected reply to register: {reply}")
        self.session = reply["session"]
        self._log("registered", reply)
        threading.Thread(target=self._reader, name="gpuq-reader", daemon=True).start()
        # report from the start, also while queued: the daemon drops sessions that stay silent
        threading.Thread(target=self._reporter, name="gpuq-reporter", daemon=True).start()
        if reply["state"] == "running":
            self._on_grant(reply)
        else:
            self.state = "queued"
            self._say(f"all GPU slots busy; queued as {self.session}")
        if not self._granted.wait(timeout):
            raise TimeoutError("no GPU slot granted in time")
        return self

    def close(self) -> None:
        self._stop.set()
        self.parker.discard_spill()
        try:
            self._send({"type": "bye"})
        except OSError:
            pass
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

    def __enter__(self) -> "GpuqClient":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------ tracking + steps
    def track(self, obj, name: str | None = None):
        """Register a module, optimizer or tensor; returns it unchanged for convenient chaining."""
        if isinstance(obj, torch.nn.Module):
            return self.parker.track_module(obj, name)
        if isinstance(obj, torch.optim.Optimizer):
            return self.parker.track_optimizer(obj, name)
        if torch.is_tensor(obj):
            return self.parker.track_tensor(obj, name)
        raise TypeError(f"cannot track {type(obj).__name__}")

    def autotrack(self, namespace: dict[str, Any] | None = None) -> list[str]:
        """Track every model, optimizer and GPU tensor in `namespace` (default: the caller's globals).

        Found objects are held weakly, so deleting a variable still frees its memory. Returns the names
        of newly tracked objects.
        """
        if namespace is None:
            frame = inspect.currentframe()
            namespace = frame.f_back.f_globals if frame and frame.f_back else {}
        added = []
        for kind, name, obj in discover(namespace):
            if self.parker.is_tracked(obj):
                continue
            self._auto_n += 1
            tname = f"auto{self._auto_n}.{name}"   # unique: a re-run cell may rebind the same variable
            if kind == "module":
                self.parker.track_module(obj, tname, weak=True)
            elif kind == "optimizer":
                self.parker.track_optimizer(obj, tname, weak=True)
            else:
                self.parker.track_tensor(obj, tname, weak=True)
            added.append(name)
        return added

    def begin_step(self) -> None:
        """Start a unit of GPU work (blocks and asks for a slot if parked). Pair with end_step()."""
        self._gpu_lock.acquire()
        while self.parker.parked and self.state != "disconnected":
            self._gpu_lock.release()
            self._wait_for_resume()
            self._gpu_lock.acquire()
        self.in_step = True

    def end_step(self, sync: bool = True) -> None:
        try:
            if sync and torch.cuda.is_available():
                torch.cuda.synchronize()  # the step's GPU work is finished before we leave the lock
        finally:
            self.in_step = False
            self.steps += 1
            self.last_step = time.time()
            self._gpu_lock.release()

    @contextmanager
    def step(self) -> Iterator[None]:
        """Wrap each unit of GPU work. Blocks (and asks for a slot) if the session is parked."""
        self.begin_step()
        ok = False
        try:
            yield
            ok = True
        finally:
            self.end_step(sync=ok)

    def snooze(self) -> None:
        """'I'm thinking, not idle': postpone the idle warning (a few times a day)."""
        self._send({"type": "snooze"})

    def away(self, minutes: float | None = None, until: float | None = None) -> None:
        """Park now and have the session restored just before you're back (`until` = epoch seconds)."""
        self._send({"type": "away", "minutes": minutes, "until": until})

    def checksum(self) -> str:
        with self._gpu_lock:
            return self.parker.checksum()

    def _wait_for_resume(self) -> None:
        if not self._resume_requested:
            self._resume_requested = True
            self._resumed.clear()
            self._say("session is parked; asking the daemon for a GPU slot")
            self._log("request", {})
            self._send({"type": "request"})
        while not self._resumed.wait(0.5):
            if self.state == "disconnected":
                break

    # ------------------------------------------------------------ background threads
    def _reader(self) -> None:
        try:
            while not self._stop.is_set():
                msg = protocol.recv(self._f)
                if msg is None:
                    break
                self._handle(msg)
        except (OSError, ValueError):
            pass
        if not self._stop.is_set():
            self.state = "disconnected"
            self._say("lost contact with the daemon; continuing without it")
            if self.parker.parked:  # fail open: bring our tensors back so the student can keep working
                with self._gpu_lock:
                    self.parker.restore()
            self._granted.set()
            self._resumed.set()

    def _handle(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        self._log(kind, msg)
        if self.on_message:
            self.on_message(msg)
        if kind == "grant":
            self._on_grant(msg)
        elif kind == "warn":
            self._say(f"WARNING: no GPU work for {msg.get('idle_s', 0):.0f} s; "
                      f"parking in {msg.get('park_in_s', 0):.0f} s unless you run something "
                      f"(or gq.snooze(), {msg.get('snoozes_left', 0)} left today)")
        elif kind == "park":
            threading.Thread(target=self._do_park, name="gpuq-park", daemon=True).start()
        elif kind == "resume":
            threading.Thread(target=self._do_resume, args=(msg,), name="gpuq-resume", daemon=True).start()
        elif kind == "spill":
            threading.Thread(target=self._do_spill, args=(msg,), name="gpuq-spill", daemon=True).start()
        elif kind == "notify":
            self.notifications.append(msg)
            del self.notifications[:-50]
            if msg.get("kind") == "next":   # the other kinds are already printed by their own messages
                self._say(f"{msg.get('title')}. {msg.get('body')}")
        elif kind == "snooze_ok":
            self._say(f"snoozed for {msg.get('snooze_s', 0):.0f} s ({msg.get('snoozes_left', 0)} left today)")
        elif kind == "away_ok":
            self._say("parking now; will be restored just before " + time.strftime(
                "%H:%M", time.localtime(msg.get("back_at") or time.time())))
        elif kind == "error":
            self._say(f"daemon says: {msg.get('error')}")

    def _on_grant(self, msg: dict[str, Any]) -> None:
        self.slot = msg.get("slot")
        frac = msg.get("cap_fraction")
        if frac and torch.cuda.is_available():
            torch.cuda.set_per_process_memory_fraction(float(frac))
        self.state = "running"
        self._say(f"got GPU slot {self.slot}" + (f" (memory cap {frac:.3f} of the GPU)" if frac else ""))
        self._granted.set()

    def _do_park(self) -> None:
        with self._gpu_lock:  # waits for a running step to finish: parks only at a safe point
            if self.parker.parked:
                return
            self.state = "parking"
            try:
                stats = self.parker.park()
            except Exception as e:  # report and stay running rather than crash the student
                self.state = "running"
                self._send({"type": "park_failed", "error": repr(e)})
                return
            self.state, self.slot, self._resume_requested = "parked", None, False
        self._say(f"parked: {stats.bytes_moved / 2**30:.2f} GiB moved to host RAM in {stats.seconds:.2f} s")
        self._send({"type": "parked", "park_s": stats.seconds, "bytes": stats.bytes_moved,
                    "reserved_after": stats.reserved_after})

    def _do_resume(self, msg: dict[str, Any]) -> None:
        with self._gpu_lock:
            if self.parker.parked:
                self.state = "resuming"
                frac = msg.get("cap_fraction")
                if frac and torch.cuda.is_available():
                    torch.cuda.set_per_process_memory_fraction(float(frac))
                try:
                    stats = self.parker.restore()
                except Exception as e:
                    self._send({"type": "resume_failed", "error": repr(e)})
                    return
                seconds = stats.seconds
            else:
                seconds = 0.0
            self.state, self.slot = "running", msg.get("slot")
        self._say(f"restored onto slot {self.slot} in {seconds:.2f} s")
        self._send({"type": "resumed", "restore_s": seconds, "slot": self.slot})
        self._resume_requested = False
        self._resumed.set()

    def _do_spill(self, msg: dict[str, Any]) -> None:
        with self._gpu_lock:
            if not self.parker.parked or self.parker.spill_path is not None:
                return
            safe_user = re.sub(r"[^A-Za-z0-9_.-]", "_", self.user)
            path = Path(msg["dir"]) / f"{safe_user}-{self.session}-{os.getpid()}.pt"
            try:
                stats = self.parker.spill(path)
            except Exception as e:
                self._send({"type": "spill_failed", "error": repr(e)})
                return
        self._say(f"parked a long time: {stats.bytes_moved / 2**30:.2f} GiB moved from RAM to disk "
                  f"in {stats.seconds:.2f} s")
        self._send({"type": "spilled", "seconds": stats.seconds, "bytes": stats.bytes_moved})

    def _reporter(self) -> None:
        while not self._stop.wait(self.report_interval):
            if self.state == "disconnected":
                return
            try:
                self._send(self.report())
            except OSError:
                return

    def report(self) -> dict[str, Any]:
        cuda = torch.cuda.is_available()
        return {"type": "report", "state": self.state, "steps": self.steps, "in_step": self.in_step,
                "last_step_ago_s": time.time() - self.last_step,
                "mem_reserved": torch.cuda.memory_reserved() if cuda else 0,
                "mem_allocated": torch.cuda.memory_allocated() if cuda else 0,
                "peak_allocated": torch.cuda.max_memory_allocated() if cuda else 0}

    # ------------------------------------------------------------ helpers
    def _send(self, msg: dict[str, Any]) -> None:
        if self._f is None:
            return
        with self._send_lock:
            protocol.send(self._f, msg)

    def _log(self, event: str, details: dict) -> None:
        self.history.append((time.time(), event, details))

    def _say(self, text: str) -> None:
        if self.verbose:
            print(f"[gpuq {self.name} {time.strftime('%H:%M:%S')}] {text}", file=sys.stderr, flush=True)
