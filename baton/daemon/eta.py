"""Queue ETA: "you're 2nd in line, about 6 min".

Each busy slot gets an estimate of when it frees up, from what the daemon already knows:

* **parking**: a second or two.
* **warned**: the rest of the grace period, then the park.
* **idle**: the rest of the idle window, plus the grace period, then the park.
* **active / restoring**: unknown (they may compute for hours), so a configured guess plus the idle
  window and grace period; marked "guess" so a page can show it with a "~".
* **whitelisted and idle**: never parked, so no estimate.

Queue position i gets the i-th earliest estimate. It is a rough guide: it ignores sessions finishing on
their own (which only makes the real wait shorter) and owners coming back to a parked session.
"""
from __future__ import annotations

from dataclasses import dataclass

PARK_S = 2.0   # time for the park itself plus one daemon tick


@dataclass
class SlotView:
    state: str | None            # None = free slot
    idle_for: float = 0.0        # seconds without compute (active/idle/warned)
    park_in_s: float = 0.0       # seconds left in the grace period (warned)
    whitelisted: bool = False


def slot_free_in(v: SlotView, idle_window_s: float, grace_s: float,
                 active_guess_s: float) -> tuple[float | None, str]:
    """(seconds until this slot is free, basis of the estimate)."""
    if v.state is None:
        return 0.0, "free"
    if v.state == "parking":
        return PARK_S, "parking"
    if v.whitelisted:
        return None, "unknown"
    if v.state == "warned":
        return v.park_in_s + PARK_S, "warned"
    if v.state == "idle":
        return max(0.0, idle_window_s - v.idle_for) + grace_s + PARK_S, "idle"
    return active_guess_s + idle_window_s + grace_s + PARK_S, "guess"


def queue_etas(slots: list[SlotView], queue_len: int, idle_window_s: float, grace_s: float,
               active_guess_s: float) -> list[tuple[float | None, str]]:
    """One (eta_s, basis) per queue position, earliest slot first; unknown slots sort last."""
    est = [slot_free_in(v, idle_window_s, grace_s, active_guess_s) for v in slots]
    est.sort(key=lambda e: (e[0] is None, e[0] or 0.0))
    out = []
    for i in range(queue_len):
        if i < len(est):
            out.append(est[i])
        else:   # more people than slots: they wait for a second turn, which we can't estimate well
            out.append((None, "unknown"))
    return out
