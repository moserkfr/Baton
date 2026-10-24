"""Write a staged daemon snapshot so the dashboard can be previewed without a GPU or a running daemon.

    python -m baton.dashboard.preview --db data/preview.db
    python -m baton.dashboard.app            # with daemon.db_path pointed at the same file

Stages: A computing on slot 0, B warned on slot 1 (countdown running), D parked and returning, C queued,
and one "saved you a wait" receipt. Uses fake time that ends at the real current time.
"""
from __future__ import annotations

import argparse
import time

from ..config import load_config, resolve_path
from ..daemon.core import Daemon
from ..daemon.monitor import FakeMonitor
from ..daemon.state import StateStore
from ..parkers.cooperative import CooperativeParker


class _Handle:
    def send(self, msg):
        pass


def stage(db_path: str) -> None:
    clock_t = [time.time() - 42]  # the staged story ends now, so the page shows 'online'
    cfg = load_config(overrides={"daemon.db_path": db_path, "daemon.dry_run": False,
                                 "policy.idle_window_s": 30, "policy.grace_period_s": 10})
    db = resolve_path(db_path)
    for suffix in ("", "-wal", "-shm"):
        p = db.with_name(db.name + suffix)
        if p.exists():
            p.unlink()
    store = StateStore(db)
    mon = FakeMonitor(6144)
    mon.used_mib = 3190
    d = Daemon(cfg, mon, CooperativeParker(), store, clock=lambda: clock_t[0])

    def join(user, pid, mib):
        sid = d.register(_Handle(), {"user": f"student-{user}", "pid": pid, "name": user})["session"]
        d.on_message(sid, {"type": "report", "mem_reserved": mib * 2**20, "steps": 0})
        return sid

    def advance(seconds, busy=()):
        for _ in range(int(seconds)):
            clock_t[0] += 1
            mon.util_by_pid = {p: 85 for p in busy}
            for sid, s in list(d.sessions.items()):
                d.on_message(sid, {"type": "report", "mem_reserved": s.mem_reserved, "steps": s.steps})
            d.tick()

    a, dd = join("A", 11, 536), join("D", 14, 1700)
    b = join("B", 12, 1152)
    advance(2, busy=[11, 14])
    d.control({"type": "park_now", "session": dd})
    d.on_message(dd, {"type": "parked", "park_s": 0.41, "bytes": int(1.5 * 2**30)})
    c = join("C", 13, 0)
    advance(3, busy=[11, 12])
    d.on_message(dd, {"type": "request"})
    advance(37, busy=[11])            # B stops computing: idle, then warned
    store.save_snapshot(clock_t[0], d.status())
    store.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/preview.db")
    args = ap.parse_args()
    stage(args.db)
    print(f"staged snapshot in {args.db}")


if __name__ == "__main__":
    main()
