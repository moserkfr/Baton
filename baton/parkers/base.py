"""The parker plug-in interface: how the daemon frees an idle session's GPU memory and gives it back.

The daemon never moves memory itself; it calls a Parker. Two implementations exist:

* `CooperativeParker` - asks the student's helper library to move its own tensors (works anywhere,
  including Windows, but only for processes that import gpuq_client).
* `CudaCheckpointParker` - asks NVIDIA's driver to suspend the whole process (`cuda-checkpoint --toggle`):
  no helper needed, frees the CUDA context too, Linux driver 570+ only.

Parking may finish immediately (a subprocess call) or later (a message comes back from the client).
`park()`/`resume()` therefore return a `ParkResult` when finished, or None when the result will arrive
later through `Daemon.on_parked` / `Daemon.on_resumed`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol


class SessionHandle(Protocol):
    """What a parker may use from a daemon session."""
    sid: str
    pid: int
    has_client: bool

    def send(self, msg: dict[str, Any]) -> None: ...


@dataclass
class ParkResult:
    ok: bool
    seconds: float = 0.0
    bytes: int = 0
    error: str = ""


class ParkerError(RuntimeError):
    """Raised when a parker cannot work at all (wrong OS, missing tool)."""


class Parker(ABC):
    name: str = "base"
    needs_client: bool = True     # True: only sessions using gpuq_client can be parked

    def can_park(self, session: SessionHandle) -> bool:
        return session.has_client or not self.needs_client

    @abstractmethod
    def park(self, session: SessionHandle) -> ParkResult | None:
        """Free the session's GPU memory. None = in progress, result arrives asynchronously."""

    @abstractmethod
    def resume(self, session: SessionHandle, slot: int, cap_fraction: float) -> ParkResult | None:
        """Give the session its GPU memory back on `slot`. None = in progress."""


def make_parker(kind: str) -> Parker:
    """Build the parker named in config.yaml (`parker.kind`)."""
    if kind == "cooperative":
        from .cooperative import CooperativeParker
        return CooperativeParker()
    if kind == "cuda_checkpoint":
        from .cuda_checkpoint import CudaCheckpointParker
        return CudaCheckpointParker()
    raise ValueError(f"unknown parker kind {kind!r}")
