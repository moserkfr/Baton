"""Impact stats: per-user and lab-wide numbers for "you gave back 12 GPU-hours this week".

Everything is computed from what the daemon already writes to SQLite: finished sessions (one row each),
the event log (receipts, waits, snoozes), and, while the daemon is running, the live snapshot.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .state import read_events, read_sessions, read_snapshot

LIVE_MAX_AGE_S = 10.0


def _blank(user: str) -> dict[str, Any]:
    return {"user": user, "sessions": 0, "gpu_h": 0.0, "given_back_h": 0.0, "parks": 0, "people_helped": 0,
            "wait_saved_for_others_h": 0.0, "wait_saved_for_you_h": 0.0, "waits": [], "snoozes": 0,
            "peak_gib": 0.0}


def user_stats(db: str | Path, since_ts: float = 0.0, now: float | None = None) -> dict[str, dict[str, Any]]:
    now = time.time() if now is None else now
    out: dict[str, dict[str, Any]] = {}

    def row(user: str) -> dict[str, Any]:
        if user not in out:
            out[user] = _blank(user)
        return out[user]

    for s in read_sessions(db, since_ts):
        r = row(s["user"])
        r["sessions"] += 1
        r["gpu_h"] += (s["gpu_seconds"] or 0) / 3600
        r["given_back_h"] += (s["parked_seconds"] or 0) / 3600
        r["parks"] += s["parks"] or 0
        r["peak_gib"] = max(r["peak_gib"], (s["peak_allocated"] or 0) / 2**30)

    snap = read_snapshot(db)
    live = snap is not None and now - snap.get("time", 0) < LIVE_MAX_AGE_S
    if live:
        for s in snap["sessions"]:
            r = row(s["user"])
            r["sessions"] += 1
            r["gpu_h"] += s.get("gpu_s", 0) / 3600
            r["given_back_h"] += s.get("parked_total_s", 0) / 3600
            r["parks"] += s.get("parks", 0)
            r["peak_gib"] = max(r["peak_gib"], s.get("peak_mib", 0) / 1024)
        for rc in snap["stats"].get("receipts", []):
            if rc.get("open"):   # closed ones are counted from the event log below
                row(rc["owner"])["wait_saved_for_others_h"] += rc["saved_s"] / 3600
                row(rc["beneficiary"])["wait_saved_for_you_h"] += rc["saved_s"] / 3600

    events = read_events(db, since_ts=since_ts, limit=1_000_000,
                         kinds=["receipt_closed", "gave_slot", "grant", "restored", "snoozed"])
    for ev in events:
        d, user = ev["detail"], ev["user"] or "?"
        if ev["kind"] == "receipt_closed":
            row(user)["wait_saved_for_others_h"] += d.get("saved_s", 0) / 3600
            row(d.get("beneficiary", "?"))["wait_saved_for_you_h"] += d.get("saved_s", 0) / 3600
        elif ev["kind"] == "gave_slot":
            row(user)["people_helped"] += 1
        elif ev["kind"] in ("grant", "restored"):
            row(user)["waits"].append(d.get("waited_s", 0.0))
        elif ev["kind"] == "snoozed":
            row(user)["snoozes"] += 1

    result = {}
    for user, r in out.items():
        waits = r.pop("waits")
        r["avg_wait_s"] = round(sum(waits) / len(waits), 1) if waits else None
        for k in ("gpu_h", "given_back_h", "wait_saved_for_others_h", "wait_saved_for_you_h", "peak_gib"):
            r[k] = round(r[k], 3)
        result[user] = r
    return result


def leaderboard(db: str | Path, days: float = 7, now: float | None = None) -> dict[str, Any]:
    """Users ranked by GPU time given back, plus lab totals, over the last `days` days."""
    now = time.time() if now is None else now
    stats = user_stats(db, now - days * 86400, now)
    ranked = sorted(stats.values(), key=lambda r: (r["given_back_h"], r["wait_saved_for_others_h"]), reverse=True)
    totals = {k: round(sum(r[k] for r in ranked), 3)
              for k in ("gpu_h", "given_back_h", "wait_saved_for_others_h", "parks", "people_helped", "sessions")}
    return {"days": days, "users": ranked, "totals": totals}
