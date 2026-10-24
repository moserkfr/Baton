"""Daemon logic with fake time, a fake monitor and fake client handles (no GPU, no sockets)."""
import pytest

from baton.config import load_config
from baton.daemon.core import Daemon
from baton.daemon.monitor import FakeMonitor
from baton.daemon.policy import (ACTIVE, IDLE, PARKED, PARKING, QUEUED, RESTORING, WARNED,
                                         PolicyConfig, decide)
from baton.daemon.queue import FairQueue, QueueEntry, UsageTracker
from baton.parkers.cooperative import CooperativeParker

MIB = 2**20


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Handle:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)

    def kinds(self):
        """Protocol messages, without the human-facing notifications."""
        return [m["type"] for m in self.sent if m["type"] != "notify"]

    def notes(self):
        return [m["kind"] for m in self.sent if m["type"] == "notify"]


def make(**over):
    base = {"policy.idle_window_s": 30, "policy.grace_period_s": 10, "policy.whitelist_users": [],
            "daemon.dry_run": False, "slots.count": 2, "slots.cap_gib": 2.2,
            "queue.returning_priority_s": 120}
    base.update(over)
    cfg = load_config(overrides=base)
    clock, mon = Clock(), FakeMonitor(6144)
    return Daemon(cfg, mon, CooperativeParker(), None, clock), clock, mon


def join(d, user, pid, mem_mib=800, whitelist=False):
    h = Handle()
    reply = d.register(h, {"type": "register", "user": user, "pid": pid, "whitelist": whitelist})
    sid = reply["session"]
    d.on_message(sid, {"type": "report", "mem_reserved": mem_mib * MIB, "steps": 0})
    return sid, h, reply


def run(d, clock, mon, seconds, busy=(), reports=()):
    """Advance time 1 s per tick; pids in `busy` show 80% utilization; sids in `reports` keep reporting."""
    for _ in range(int(seconds)):
        clock.t += 1
        mon.util_by_pid = {pid: 80 for pid in busy}
        for sid in list(d.sessions):
            s = d.sessions[sid]
            d.on_message(sid, {"type": "report", "mem_reserved": s.mem_reserved, "steps": s.steps})
        d.tick()


def test_demo_storyline_park_queue_restore():
    d, clock, mon = make()
    a, ha, ra = join(d, "A", 101)
    b, hb, rb = join(d, "B", 102, mem_mib=900)
    assert ra["state"] == rb["state"] == "running"
    c, hc, rc = join(d, "C", 103)
    assert rc["state"] == "queued" and d.sessions[c].state == QUEUED

    run(d, clock, mon, 5, busy=[101, 102])            # both computing
    assert d.sessions[b].state == ACTIVE
    run(d, clock, mon, 5, busy=[101])                 # B stops computing
    assert d.sessions[b].state == IDLE
    run(d, clock, mon, 25, busy=[101])                # B idle 30 s -> warned
    assert d.sessions[b].state == WARNED and "warn" in hb.kinds()
    run(d, clock, mon, 10, busy=[101])                # grace over -> park requested
    assert d.sessions[b].state == PARKING and hb.kinds()[-1] == "park"
    d.on_message(b, {"type": "parked", "park_s": 1.1, "bytes": 900 * MIB})
    assert d.sessions[b].state == PARKED
    assert d.sessions[c].state == ACTIVE and hc.kinds()[-1] == "grant"   # C got B's slot
    assert d.receipts and d.receipts[0].beneficiary == "C" and d.receipts[0].owner == "B"
    assert d.sessions[a].state == ACTIVE                                 # A untouched

    run(d, clock, mon, 20, busy=[101, 103])
    d.on_message(b, {"type": "request"})              # B comes back: no free slot, queued with priority
    assert b in d.queue and d.sessions[b].state == PARKED
    assert d.receipts[0].closed_at is not None and d.receipts[0].saved_s(clock.t) == pytest.approx(20, abs=1)
    d.on_message(c, {"type": "bye"})                  # C finishes, slot frees
    assert d.sessions[b].state == RESTORING and hb.kinds()[-1] == "resume"
    d.on_message(b, {"type": "resumed", "restore_s": 0.25})
    assert d.sessions[b].state == ACTIVE and d.sessions[b].restores == 1
    assert d.reclaimed_seconds() == pytest.approx(20, abs=1)
    assert d.total_parks == 1 and d.total_restores == 1


def test_owner_returning_to_free_slot_restores_immediately():
    d, clock, mon = make(**{"slots.count": 1})
    b, hb, _ = join(d, "B", 102)
    run(d, clock, mon, 41)
    assert d.sessions[b].state == PARKING
    d.on_message(b, {"type": "parked", "park_s": 1.0, "bytes": 1})
    d.on_message(b, {"type": "request"})
    assert d.sessions[b].state == RESTORING and hb.sent[-1]["slot"] == 0


def test_request_during_parking_is_handled_after_park():
    d, clock, mon = make(**{"slots.count": 1})
    b, hb, _ = join(d, "B", 102)
    run(d, clock, mon, 41)
    d.on_message(b, {"type": "request"})              # arrives while the client is still parking
    assert d.sessions[b].state == PARKING
    d.on_message(b, {"type": "parked", "park_s": 1.0, "bytes": 1})
    assert d.sessions[b].state == RESTORING


def test_warning_cancelled_when_work_resumes():
    d, clock, mon = make()
    b, hb, _ = join(d, "B", 102)
    run(d, clock, mon, 32)
    assert d.sessions[b].state == WARNED
    run(d, clock, mon, 3, busy=[102])
    assert d.sessions[b].state == ACTIVE
    assert any(e["kind"] == "warn_cancelled" for e in d.events)
    run(d, clock, mon, 20)
    assert d.sessions[b].state != PARKING             # the idle clock restarted


def test_client_steps_count_as_activity_even_at_zero_util():
    d, clock, mon = make()
    b, hb, _ = join(d, "B", 102)
    for _ in range(60):                               # data-loading-bound training: 0% NVML, steps advance
        clock.t += 1
        d.sessions[b].steps += 1
        d.on_message(b, {"type": "report", "mem_reserved": 800 * MIB, "steps": d.sessions[b].steps})
        d.tick()
    assert d.sessions[b].state == ACTIVE and "warn" not in hb.kinds()


def test_safety_rules_whitelist_and_small_memory():
    d, clock, mon = make(**{"policy.whitelist_users": ["W"]})
    w, hw, _ = join(d, "W", 201)
    small, hs, _ = join(d, "S", 202, mem_mib=10)
    run(d, clock, mon, 120)
    assert d.sessions[w].state == IDLE and "warn" not in hw.kinds()
    assert d.sessions[small].state == IDLE and "warn" not in hs.kinds()


def test_client_whitelist_flag():
    d, clock, mon = make()
    w, hw, _ = join(d, "X", 201, whitelist=True)
    run(d, clock, mon, 60)
    assert "warn" not in hw.kinds()


def test_dry_run_logs_but_never_sends():
    d, clock, mon = make(**{"daemon.dry_run": True})
    b, hb, _ = join(d, "B", 102)
    run(d, clock, mon, 45)
    kinds = [e["kind"] for e in d.events]
    assert "would_warn" in kinds and "would_park" in kinds
    assert "warn" not in hb.kinds() and "park" not in hb.kinds()
    assert d.sessions[b].state in (IDLE, WARNED)


def test_silent_client_is_dropped_and_slot_freed():
    d, clock, mon = make(**{"slots.count": 1, "daemon.report_timeout_s": 15})
    b, hb, _ = join(d, "B", 102)
    c, hc, _ = join(d, "C", 103)
    for _ in range(16):                               # nobody reports, B goes silent
        clock.t += 1
        d.on_message(c, {"type": "report", "mem_reserved": 0, "steps": 0})
        d.tick()
    assert b not in d.sessions
    assert d.sessions[c].state == ACTIVE and hc.kinds()[-1] == "grant"


def test_park_timeout_returns_session_to_active():
    d, clock, mon = make(**{"parker.park_timeout_s": 5})
    b, hb, _ = join(d, "B", 102)
    run(d, clock, mon, 41)
    assert d.sessions[b].state == PARKING
    run(d, clock, mon, 6)                             # client never acks
    assert d.sessions[b].state == ACTIVE
    assert any(e["kind"] == "park_timeout" for e in d.events)


def test_manual_park_now_and_resume_controls():
    d, clock, mon = make()
    b, hb, _ = join(d, "B", 102)
    assert d.control({"type": "park_now", "session": "B"})["type"] == "ok"
    assert d.sessions[b].state == PARKING
    d.on_message(b, {"type": "parked", "park_s": 1.0, "bytes": 1})
    assert d.control({"type": "resume", "session": b})["type"] == "ok"
    assert d.sessions[b].state == RESTORING
    assert d.control({"type": "park_now", "session": "nobody"})["type"] == "error"
    st = d.control({"type": "status"})["status"]
    assert st["slots"][0]["holder"]["user"] == "B"


def test_new_session_does_not_jump_existing_queue():
    d, clock, mon = make(**{"slots.count": 1})
    a, _, _ = join(d, "A", 1)
    b, _, rb = join(d, "B", 2)
    assert rb["state"] == "queued"
    d.on_message(a, {"type": "bye"})
    assert d.sessions[b].state == ACTIVE


def test_disconnect_while_parked_counts_reclaimed_time():
    d, clock, mon = make(**{"slots.count": 1})
    b, hb, _ = join(d, "B", 102)
    run(d, clock, mon, 41)
    d.on_message(b, {"type": "parked", "park_s": 1.0, "bytes": 1})
    clock.t += 100
    d.disconnect(b)
    assert d.reclaimed_seconds() == pytest.approx(100)


# ---------------------------------------------------------------- pure policy + queue

def test_decide_transitions():
    cfg = PolicyConfig(idle_window_s=30, grace_period_s=10, min_memory_mib=64, never_park_above_pct=30)
    assert decide(ACTIVE, 0, 0, 500, 80, False, cfg) is None
    assert decide(ACTIVE, 5, 0, 500, 0, False, cfg) == "idle"
    assert decide(IDLE, 30, 0, 500, 0, False, cfg) == "warn"
    assert decide(WARNED, 35, 5, 500, 0, False, cfg) is None
    assert decide(WARNED, 40, 10, 500, 0, False, cfg) == "park"
    assert decide(WARNED, 40, 10, 500, 50, False, cfg) is None      # never park above 30% util
    assert decide(WARNED, 0, 3, 500, 80, False, cfg) == "activate"
    assert decide(IDLE, 999, 0, 500, 0, True, cfg) is None          # whitelisted
    assert decide(PARKED, 999, 0, 500, 0, False, cfg) is None


def test_fair_queue_orders_by_wait_minus_usage_and_returning_priority():
    usage = UsageTracker(24)
    q = FairQueue(usage, usage_weight_s_per_gpuh=600, returning_priority_s=120)
    usage.add("heavy", 3 * 3600, now=0)                # 3 recent GPU-hours = 1800 s penalty
    q.push(QueueEntry("s1", "heavy", since=0))
    q.push(QueueEntry("s2", "light", since=1000))
    assert q.pop(now=1200).sid == "s2"                 # waited 200 s but heavy is penalised 1800 s
    q.push(QueueEntry("s3", "light2", since=1100))
    q.push(QueueEntry("s4", "owner", since=1190, returning=True))
    assert q.pop(now=1200).sid == "s4"                 # returning owner within its window
    q.push(QueueEntry("s5", "owner2", since=0, returning=True))
    assert q.pop(now=5000).sid == "s5"                 # window expired, but longest wait anyway


def test_usage_decays_with_half_life():
    u = UsageTracker(halflife_h=1)
    u.add("x", 3600, now=0)
    assert u.get("x", now=3600) == pytest.approx(0.5)
