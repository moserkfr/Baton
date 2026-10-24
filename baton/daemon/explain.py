"""'Why was I parked?': turn a session's raw events into plain sentences.

Students accept a tool that parks their work only if they can see exactly why it happened. Every daemon
decision is already in the event log, so the timeline is just those events, explained.
"""
from __future__ import annotations

from typing import Any


def _mins(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    seconds = float(seconds)
    if seconds < 90:
        return f"{seconds:.0f} s"
    return f"{seconds / 60:.0f} min"


def describe(ev: dict[str, Any]) -> str:
    """One sentence for one event (unknown kinds fall back to the raw kind)."""
    d = ev.get("detail") or {}
    k = ev["kind"]
    texts = {
        "register": "Session started.",
        "queued": f"All GPU slots were busy, so you joined the queue at position {d.get('position', '?')}.",
        "grant": f"Got GPU slot {d.get('slot')}" + (f" after waiting {_mins(d.get('waited_s'))}." if d.get("waited_s")
                                                    else " right away."),
        "active": "GPU work started again.",
        "idle": f"No GPU work for a few seconds while holding {d.get('mem_mib', '?')} MiB of GPU memory.",
        "warn": f"Warned: no GPU work for {_mins(d.get('idle_s'))}. Parking in {_mins(d.get('park_in_s'))} "
                "unless you run something or snooze.",
        "warn_cancelled": "GPU work resumed before the deadline, so the warning was withdrawn.",
        "snoozed": f"You snoozed: no warning before {_mins(d.get('for_s'))} from now "
                   f"({d.get('left', '?')} snoozes left today).",
        "away": f"You said you'd be away until {d.get('until_text', '?')}, so the session was parked right away.",
        "park_now": "Parked by hand from the dashboard or CLI.",
        "park": f"Parking started ({d.get('reason', '')}): {d.get('mem_mib', '?')} MiB moving to host RAM.",
        "parked": f"Parked in {d.get('park_s', 0):.2f} s. {d.get('gib', 0):.2f} GiB is safe in RAM; the process "
                  "kept running.",
        "park_failed": f"Parking failed ({d.get('error', '')}); the session kept its slot.",
        "park_timeout": "Parking took too long, so the session kept its slot.",
        "cannot_park": "Idle, but this process can't be parked (it doesn't use the helper library).",
        "gave_slot": f"Your freed slot went to {d.get('to_user', 'someone')}, who had waited "
                     f"{_mins(d.get('waited_s'))}.",
        "receipt_closed": f"Your park saved {d.get('beneficiary', 'someone')} at least "
                          f"{_mins(d.get('saved_s'))} of waiting.",
        "spilled": f"Parked for a long time, so the state moved from RAM to disk ({d.get('gib', 0):.2f} GiB in "
                   f"{d.get('seconds', 0):.1f} s).",
        "spill_failed": f"Moving to disk failed ({d.get('error', '')}); the state stays in RAM.",
        "prewarm": "Restoring a little before your announced return, so it's ready when you are.",
        "owner_returned": "You came back and asked for the GPU" + (
            "; a slot was free." if d.get("free_slots") else "; you jumped to the front of the queue."),
        "restoring": f"Restoring onto slot {d.get('slot')}.",
        "restored": f"Restored in {d.get('restore_s', 0):.2f} s, every tensor exactly as you left it.",
        "resume_failed": f"Restore failed ({d.get('error', '')}).",
        "would_warn": f"(Dry run) would have warned after {_mins(d.get('idle_s'))} without GPU work.",
        "would_park": f"(Dry run) would have parked after {_mins(d.get('idle_s'))} without GPU work.",
        "would_restore": f"(Dry run) would have restored; {_mins(d.get('would_reclaim_s'))} of GPU time could "
                         "have gone to someone else.",
        "notify": f"Notification: {d.get('title', '')}",
        "receipt": f"Got a slot freed by parking {d.get('from_user', 'someone')}.",
        "gone": f"Session ended ({d.get('reason', '')}).",
    }
    return texts.get(k, k.replace("_", " ").capitalize() + ".")


def timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Events (oldest first) with a `text` field added."""
    return [{**ev, "text": describe(ev)} for ev in sorted(events, key=lambda e: (e["ts"], e.get("id", 0)))]
