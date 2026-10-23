"""The four reclaim policies compared by the simulator.

A policy answers three questions for a session that holds a GPU and is in an idle gap:
what counts as idle, when to act, and whether acting keeps or loses the user's state.

| kind       | idle means                         | action                             | state |
|------------|------------------------------------|------------------------------------|-------|
| hold       | -                                  | none: GPU held until logout        | kept  |
| culler     | no web activity (unattended gap)   | kill after `idle_window`           | lost  |
| gpu_cancel | no GPU compute                     | warn at `warn_after`, kill at `idle_window` | lost |
| park       | no GPU compute                     | warn at `idle_window`, park after `grace`   | kept  |
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Policy:
    name: str
    kind: str                       # hold | culler | gpu_cancel | park
    idle_window: float = 0.0        # minutes of idleness before the decisive action (kill or warn)
    warn_after: float | None = None  # gpu_cancel only: warning time (no effect on the outcome)
    grace: float = 0.0              # park only: minutes between warning and park
    park_s: float = 1.2             # park only: seconds to move GPU memory to host RAM
    restore_s: float = 0.27         # park only: seconds to move it back
    returning_priority: bool = False  # park only: returning owners jump the queue for a short window

    @property
    def loses_state(self) -> bool:
        return self.kind in ("culler", "gpu_cancel")


def hold_until_logout() -> Policy:
    return Policy("Hold until logout", "hold")


def activity_culler(window_min: float = 30.0) -> Policy:
    return Policy(f"Activity culler (kill after {window_min:g} min)", "culler", idle_window=window_min)


def gpu_aware_cancel(warn_min: float = 45.0, cancel_min: float = 90.0) -> Policy:
    return Policy(f"GPU-aware cancel (warn {warn_min:g}, cancel {cancel_min:g} min)", "gpu_cancel",
                  idle_window=cancel_min, warn_after=warn_min)


def park_and_restore(window_min: float = 30.0, grace_min: float = 5.0,
                     park_s: float = 1.2, restore_s: float = 0.27) -> Policy:
    return Policy(f"Park and restore (idle {window_min:g} min, ours)", "park", idle_window=window_min,
                  grace=grace_min, park_s=park_s, restore_s=restore_s, returning_priority=True)


def standard_policies(idle_window_min: float = 30.0, park_s: float = 1.2, restore_s: float = 0.27,
                      grace_min: float = 5.0) -> list[Policy]:
    """The four policies from the evaluation design, in presentation order."""
    return [
        hold_until_logout(),
        activity_culler(30.0),
        gpu_aware_cancel(45.0, 90.0),
        park_and_restore(idle_window_min, grace_min, park_s, restore_s),
    ]
