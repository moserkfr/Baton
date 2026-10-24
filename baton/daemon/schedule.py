"""Deadline mode: stricter idle windows on chosen days, from config or switched on at runtime.

The week an assignment is due, every GPU-hour counts, so a lab admin can shorten the idle window and
grace period for that week only. Precedence: a runtime override (`gpuq mode ...`) beats a configured
schedule, which beats the normal policy.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
from typing import Any

OVERRIDABLE = ("idle_window_s", "grace_period_s")


def _to_ts(value: Any, end: bool = False) -> float:
    """YAML date, datetime, ISO string or epoch seconds -> epoch seconds (local time).

    A date without a time means the start of that day, or for `end` the end of that day (inclusive).
    """
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        value = datetime.fromisoformat(value) if (":" in value or "T" in value) else date.fromisoformat(value)
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, date):
        day = datetime.combine(value, dtime.min)
        return (day + timedelta(days=1)).timestamp() if end else day.timestamp()
    raise ValueError(f"cannot read a time from {value!r}")


def active_schedule(schedules: list[dict[str, Any]], now: float) -> dict[str, Any] | None:
    """The first configured schedule whose [start, end] contains `now`, or None."""
    for sch in schedules or []:
        if _to_ts(sch["start"]) <= now < _to_ts(sch["end"], end=True):
            return sch
    return None


def effective_overrides(schedules: list[dict[str, Any]], runtime: dict[str, Any] | None,
                        now: float) -> tuple[dict[str, float], dict[str, Any] | None]:
    """(policy fields to override, description of the active mode or None)."""
    if runtime and (runtime.get("until") is None or now < runtime["until"]):
        src, origin = runtime, "manual"
    else:
        src, origin = active_schedule(schedules, now), "schedule"
    if not src:
        return {}, None
    fields = {k: float(src[k]) for k in OVERRIDABLE if src.get(k) is not None}
    until = src.get("until") if origin == "manual" else _to_ts(src["end"], end=True)
    return fields, {"name": src.get("name", "deadline mode"), "source": origin, "until": until, **fields}
