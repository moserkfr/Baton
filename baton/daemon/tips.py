"""Memory right-sizing tips from each session's measured peak.

Students usually ask for "a whole GPU" without knowing what their notebook needs. The helper library
reports PyTorch's peak allocation, so the daemon can say "you used at most 1.1 GiB of your 2.2 GiB slot"
(a smaller slot would let more people share the GPU) or warn when a session is close to its cap.
"""
from __future__ import annotations

import math
from typing import Any

GIB = 2**30


def memory_tip(peak_allocated: int, cap_bytes: int, steps: int) -> dict[str, Any] | None:
    """A tip dict ({kind, text, suggested_gib}) or None when there's nothing useful to say yet."""
    if steps < 1 or peak_allocated <= 0 or cap_bytes <= 0:
        return None
    peak, cap = peak_allocated / GIB, cap_bytes / GIB
    ratio = peak_allocated / cap_bytes
    if ratio >= 0.9:
        return {"kind": "near_cap", "peak_gib": round(peak, 2), "cap_gib": round(cap, 2), "suggested_gib": None,
                "text": f"Peak {peak:.2f} of your {cap:.2f} GiB slot: close to running out of memory. "
                        "A smaller batch size or a bigger slot would avoid crashes."}
    if ratio <= 0.5:
        suggested = max(0.25, math.ceil(peak * 1.25 * 4) / 4)   # 25 % headroom, rounded up to 0.25 GiB
        return {"kind": "oversized", "peak_gib": round(peak, 2), "cap_gib": round(cap, 2),
                "suggested_gib": suggested,
                "text": f"You used at most {peak:.2f} of your {cap:.2f} GiB slot. Asking for {suggested:g} GiB "
                        "would leave room for someone else, and you'd get a slot sooner."}
    return None
