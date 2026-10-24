"""Policy engine: pure functions that decide what should happen to a session. No I/O, no clocks.

Session lifecycle (7 states):

    queued -> active <-> idle -> warned -> parking -> parked -> restoring -> active
                 ^                  |  (GPU work again before the grace ends: back to active)
                 +------------------+

* **active**: computing now (NVML utilization above threshold, or the client's step counter moved).
* **idle**: holds a slot but has not computed for a few seconds (shown red on the dashboard).
* **warned**: idle for the whole idle window; the owner has been told and has `grace_period_s` to react.
* **parking / parked**: memory is moving / has moved to host RAM; the slot is free for someone else.
* **restoring**: the owner came back and got a slot; memory is moving back.

Safety rules, all applied here: whitelisted users are never parked; sessions holding less than
`min_memory_mib` are not worth parking; a session whose utilization is above `never_park_above_pct`
at the moment of parking is not parked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

QUEUED, ACTIVE, IDLE, WARNED, PARKING, PARKED, RESTORING = (
    "queued", "active", "idle", "warned", "parking", "parked", "restoring")
HOLDING_SLOT = (ACTIVE, IDLE, WARNED, PARKING, RESTORING)


@dataclass
class PolicyConfig:
    idle_window_s: float = 30.0
    grace_period_s: float = 10.0
    util_threshold_pct: float = 5.0
    min_memory_mib: float = 64.0
    never_park_above_pct: float = 30.0
    whitelist_users: list[str] = field(default_factory=list)
    idle_display_s: float = 3.0

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "PolicyConfig":
        p = cfg["policy"]
        return cls(p["idle_window_s"], p["grace_period_s"], p["util_threshold_pct"], p["min_memory_mib"],
                   p["never_park_above_pct"], list(p.get("whitelist_users") or []),
                   p.get("idle_display_s", 3.0))


def is_computing(util_pct: float, steps_advanced: bool, cfg: PolicyConfig) -> bool:
    """GPU compute above the threshold, or the client finished a step since the last poll.

    The step signal is a safety net: a training loop paused on CPU-side data loading shows 0% on NVML
    but keeps finishing steps, and must not be mistaken for an abandoned notebook.
    """
    return util_pct > cfg.util_threshold_pct or steps_advanced


def decide(state: str, idle_for: float, warned_for: float, mem_mib: float, util_pct: float,
           whitelisted: bool, cfg: PolicyConfig) -> str | None:
    """Next action for a session holding a slot: activate | idle | warn | park | None (no change).

    idle_for: seconds since the session last computed (0 = computing right now).
    warned_for: seconds since the warning (only meaningful in state WARNED).
    """
    if state not in (ACTIVE, IDLE, WARNED):
        return None
    if idle_for <= 0:
        return "activate" if state != ACTIVE else None
    parkable = not whitelisted and mem_mib >= cfg.min_memory_mib
    if state == WARNED:
        if not parkable:
            return "activate"  # e.g. whitelisted meanwhile, or memory dropped: withdraw the warning
        if warned_for >= cfg.grace_period_s:
            return None if util_pct > cfg.never_park_above_pct else "park"
        return None
    if idle_for >= cfg.idle_window_s and parkable:
        return "warn"
    if state == ACTIVE and idle_for >= cfg.idle_display_s:
        return "idle"
    return None
