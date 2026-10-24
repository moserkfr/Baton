"""cuda-checkpoint parker: NVIDIA's driver suspends the whole process's GPU state. Linux only.

How it works on Linux (driver 550+, process trees and lock timeouts from 570+):
    cuda-checkpoint --get-state --pid <pid>   -> running | locked | checkpointed | failed
    cuda-checkpoint --toggle --pid <pid>      running -> checkpointed (park) / checkpointed -> running (restore)
Suspending locks the process's CUDA API, waits for submitted work to finish, copies all device memory
to host memory and releases every GPU resource, *including the CUDA context* (the ~100-145 MiB our
cooperative parker cannot free). CPU threads keep running; any CUDA call simply blocks until resume.
So the student needs no helper library at all: this is the "transparent" parker.

Why it is a stub here: cuda-checkpoint is a Linux binary and does not run on Windows (this demo laptop).
The class builds the real commands and is unit-tested with a fake runner; constructing it on another OS
raises ParkerError with instructions.

Enabling it on a Linux lab server:
    1. NVIDIA driver 570 or newer (`nvidia-smi` shows the version); no UVM-managed memory in the workload.
    2. Put the `cuda-checkpoint` binary (github.com/NVIDIA/cuda-checkpoint) on PATH.
    3. Run the daemon as the same user as the students' processes, or as root.
    4. In config.yaml set `parker.kind: cuda_checkpoint`.
Limitation in this codebase: the daemon still learns about sessions from gpuq_client registrations. A full
Linux deployment would also discover processes from NVML's process list (per-process memory works on
Linux) and detect an owner's return with `gpuq resume` or a thin "I'm back" hook.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from typing import Callable

from .base import Parker, ParkerError, ParkResult, SessionHandle

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


class CudaCheckpointParker(Parker):
    name = "cuda_checkpoint"
    needs_client = False

    def __init__(self, binary: str = "cuda-checkpoint", runner: Runner | None = None,
                 platform: str | None = None):
        platform = platform or sys.platform
        if not platform.startswith("linux"):
            raise ParkerError(
                f"cuda-checkpoint is not supported on this OS ({platform}). It needs native Linux with "
                "NVIDIA driver 570+. Use `parker.kind: cooperative` here; see parkers/cuda_checkpoint.py "
                "for how to enable it on a Linux lab server.")
        if runner is None and shutil.which(binary) is None:
            raise ParkerError(f"`{binary}` not found on PATH (github.com/NVIDIA/cuda-checkpoint)")
        self.binary = binary
        self.run = runner or _default_runner

    def get_state(self, pid: int) -> str:
        r = self.run([self.binary, "--get-state", "--pid", str(pid)])
        if r.returncode != 0:
            raise ParkerError(f"get-state failed for pid {pid}: {r.stderr.strip()}")
        return r.stdout.strip()

    def _toggle(self, pid: int, expect_from: str) -> ParkResult:
        try:
            state = self.get_state(pid)
        except ParkerError as e:
            return ParkResult(False, error=str(e))
        if state != expect_from:
            return ParkResult(False, error=f"pid {pid} is {state!r}, expected {expect_from!r}")
        t0 = time.perf_counter()
        r = self.run([self.binary, "--toggle", "--pid", str(pid)])
        if r.returncode != 0:
            return ParkResult(False, error=f"toggle failed: {r.stderr.strip()}")
        return ParkResult(True, seconds=time.perf_counter() - t0)

    def park(self, session: SessionHandle) -> ParkResult | None:
        return self._toggle(session.pid, "running")

    def resume(self, session: SessionHandle, slot: int, cap_fraction: float) -> ParkResult | None:
        # The driver restores memory to the same GPU and addresses; a slot is bookkeeping here.
        # (Driver 580 can also migrate a process to another GPU, which would map to real lab GPUs.)
        return self._toggle(session.pid, "checkpointed")
