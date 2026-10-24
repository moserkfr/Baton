"""Cooperative parker: the daemon asks the student's own helper library to park and restore itself.

The heavy lifting (moving tensors to reused pinned host buffers, restoring each to its original device)
happens inside the student process, in `baton.client.tensor_parking`. Only the student process
knows which tensors it owns and when it is safe to touch them (between steps), so it is the right place.

Trade-off: a process that does not import gpuq_client can be *detected* as idle but not parked.
"""
from __future__ import annotations

from .base import Parker, ParkResult, SessionHandle


class CooperativeParker(Parker):
    name = "cooperative"
    needs_client = True

    def park(self, session: SessionHandle) -> ParkResult | None:
        session.send({"type": "park"})
        return None  # the client answers with `parked` (or `park_failed`)

    def resume(self, session: SessionHandle, slot: int, cap_fraction: float) -> ParkResult | None:
        session.send({"type": "resume", "slot": slot, "cap_fraction": cap_fraction})
        return None  # the client answers with `resumed`

    def spill(self, session: SessionHandle, directory: str) -> ParkResult | None:
        session.send({"type": "spill", "dir": directory})
        return None  # the client answers with `spilled` (or `spill_failed`)
