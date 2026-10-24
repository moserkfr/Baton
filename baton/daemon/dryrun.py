"""Dry-run report: "here is what we would have reclaimed on *your* lab".

Run the daemon with `--dry-run` for a week: it warns and parks nobody, but logs `would_park` when it
would have parked a session and `would_restore` when that session computed again (or ended). This module
turns those events into the numbers a lab admin needs before switching parking on.

    python -m baton.daemon.dryrun            # report from data/state.db
"""
from __future__ import annotations

import argparse
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..config import load_config, resolve_path
from .state import read_events, read_snapshot


def dryrun_report(db: str | Path, since_ts: float = 0.0, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    events = sorted(read_events(db, since_ts=since_ts, limit=1_000_000,
                                kinds=["register", "would_park", "would_restore", "gone"]), key=lambda e: e["id"])
    open_park: dict[str, tuple[float, str]] = {}            # sid -> (would_park ts, user)
    per_user: dict[str, dict[str, float]] = defaultdict(lambda: {"would_parks": 0, "would_reclaim_h": 0.0})
    longest, n_parks = 0.0, 0
    for ev in events:
        sid, user = ev["sid"], ev["user"] or "?"
        if ev["kind"] == "register":
            open_park.pop(sid, None)                         # session ids restart with each daemon run
        elif ev["kind"] == "would_park":
            open_park[sid] = (ev["ts"], user)
            per_user[user]["would_parks"] += 1
            n_parks += 1
        elif ev["kind"] == "would_restore" and sid in open_park:
            open_park.pop(sid)
            secs = float(ev["detail"].get("would_reclaim_s", 0.0))
            per_user[user]["would_reclaim_h"] += secs / 3600
            longest = max(longest, secs)

    snap = read_snapshot(db)
    live = snap is not None and now - snap.get("time", 0) < 10
    for ts, user in open_park.values():                      # still "parked" right now (or the daemon stopped)
        secs = (now if live else (snap or {}).get("time", ts)) - ts
        per_user[user]["would_reclaim_h"] += max(0.0, secs) / 3600
        longest = max(longest, secs)

    all_ts = [e["ts"] for e in events]
    window_h = (max(all_ts) - min(all_ts)) / 3600 if all_ts else 0.0
    reclaim_h = sum(u["would_reclaim_h"] for u in per_user.values())
    users = sorted(({"user": k, "would_parks": int(v["would_parks"]), "would_reclaim_h": round(v["would_reclaim_h"], 3)}
                    for k, v in per_user.items()), key=lambda r: r["would_reclaim_h"], reverse=True)
    stats = (snap or {}).get("stats", {})
    report = {
        "window_h": round(window_h, 2),
        "would_parks": n_parks,
        "users_affected": len(users),
        "would_reclaim_gpu_h": round(reclaim_h, 3),
        "longest_park_h": round(longest / 3600, 3),
        "queue_wait_parking_could_serve_h": round(stats.get("dry_saved_wait_s", 0.0) / 3600, 3),
        "per_user": users,
        "daemon_in_dry_run": bool((snap or {}).get("dry_run")),
    }
    report["text"] = to_markdown(report)
    return report


def to_markdown(r: dict[str, Any]) -> str:
    lines = [f"# Dry-run report ({r['window_h']:.1f} h observed)", "",
             f"- Would have parked **{r['would_parks']}** times, across **{r['users_affected']}** users.",
             f"- GPU time that could have gone to someone else: **{r['would_reclaim_gpu_h']:.2f} GPU-hours** "
             f"(longest single park {r['longest_park_h']:.2f} h).",
             f"- Queue waiting those slots could have served (current daemon run): "
             f"**{r['queue_wait_parking_could_serve_h']:.2f} h**.", ""]
    if r["per_user"]:
        lines += ["| User | Would park | Could reclaim (GPU-h) |", "| --- | --- | --- |"]
        lines += [f"| {u['user']} | {u['would_parks']} | {u['would_reclaim_h']:.2f} |" for u in r["per_user"]]
    else:
        lines.append("No idle sessions were seen yet. Leave the dry run going longer.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Summarise what a dry run would have reclaimed")
    ap.add_argument("--db", default=None)
    ap.add_argument("--days", type=float, default=None, help="only the last N days")
    args = ap.parse_args(argv)
    db = args.db or resolve_path(load_config()["daemon"]["db_path"])
    since = time.time() - args.days * 86400 if args.days else 0.0
    print(dryrun_report(db, since)["text"])


if __name__ == "__main__":
    main()
