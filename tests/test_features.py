"""The user-friendly features, with fake time and no GPU: away, snooze, queue ETA, notifications,
'why was I parked', right-sizing tips, spill to disk, deadline mode, impact stats and the dry-run report."""
import time
from datetime import date, datetime, timedelta

import pytest

from baton.daemon.dryrun import dryrun_report
from baton.daemon.eta import SlotView, queue_etas, slot_free_in
from baton.daemon.explain import describe, timeline
from baton.daemon.notify import Notifier, webhook_payload
from baton.daemon.policy import ACTIVE, IDLE, PARKED, PARKING, QUEUED, RESTORING, WARNED
from baton.daemon.schedule import active_schedule, effective_overrides
from baton.daemon.state import StateStore, read_events, read_session_events
from baton.daemon.stats import leaderboard, user_stats
from baton.daemon.tips import memory_tip
from test_daemon import MIB, join, make, run


def park(d, sid, clock, mon, busy=()):
    """Run until `sid` is asked to park, then acknowledge like a real client would."""
    for _ in range(120):
        if d.sessions[sid].state == PARKING:
            break
        run(d, clock, mon, 1, busy=busy)
    d.on_message(sid, {"type": "parked", "park_s": 0.3, "bytes": 2**30})
    assert d.sessions[sid].state == PARKED


# ---------------------------------------------------------------- away ("back at 3pm")

def test_away_parks_now_and_prewarms_before_return():
    d, clock, mon = make(**{"policy.away_prewarm_s": 10})
    b, hb, _ = join(d, "B", 102)
    reply = d.away(d.sessions[b], minutes=1)
    assert reply["type"] == "ok" and d.sessions[b].state == PARKING and hb.kinds()[-1] == "park"
    d.on_message(b, {"type": "parked", "park_s": 0.3, "bytes": 2**30})
    run(d, clock, mon, 45)
    assert d.sessions[b].state == PARKED                       # still away
    run(d, clock, mon, 6)                                       # 51 s: within 10 s of the return
    assert d.sessions[b].state == RESTORING and hb.kinds()[-1] == "resume"
    d.on_message(b, {"type": "resumed", "restore_s": 0.1})
    assert d.sessions[b].state == ACTIVE
    assert "parked" in hb.notes() and "restored" in hb.notes()
    assert [e["kind"] for e in d.events].count("prewarm") == 1


def test_away_from_client_message_and_errors():
    d, clock, mon = make()
    b, hb, _ = join(d, "B", 102)
    d.on_message(b, {"type": "away", "minutes": None, "until": None})
    assert hb.kinds()[-1] == "error"
    d.on_message(b, {"type": "away", "until": clock.t - 5})
    assert hb.kinds()[-1] == "error"
    d.on_message(b, {"type": "away", "minutes": 30})
    assert "away_ok" in hb.kinds() and d.sessions[b].back_at == pytest.approx(clock.t + 1800)


def test_manual_resume_cancels_away():
    d, clock, mon = make()
    b, hb, _ = join(d, "B", 102)
    d.control({"type": "away", "session": "B", "minutes": 5})
    d.on_message(b, {"type": "parked", "park_s": 0.3, "bytes": 1})
    assert d.control({"type": "resume", "session": "B"})["type"] == "ok"
    assert d.sessions[b].back_at is None and d.sessions[b].state == RESTORING


# ---------------------------------------------------------------- snooze

def test_snooze_withdraws_warning_and_delays_the_next_one():
    d, clock, mon = make(**{"policy.snooze_s": 60})
    b, hb, _ = join(d, "B", 102)
    run(d, clock, mon, 31)
    assert d.sessions[b].state == WARNED
    reply = d.control({"type": "snooze", "session": "B"})
    assert reply["type"] == "ok" and reply["snoozes_left"] == 2
    assert d.sessions[b].state == IDLE
    run(d, clock, mon, 59)
    assert d.sessions[b].state == IDLE                          # no warning during the snooze
    run(d, clock, mon, 2)
    assert d.sessions[b].state == WARNED                        # warned right when the snooze ends
    assert hb.kinds().count("warn") == 2


def test_snooze_daily_limit():
    d, clock, mon = make(**{"policy.snoozes_per_day": 2})
    b, hb, _ = join(d, "B", 102)
    assert d.snooze(d.sessions[b])["type"] == "ok"
    assert d.snooze(d.sessions[b])["type"] == "ok"
    err = d.snooze(d.sessions[b])
    assert err["type"] == "error" and "no snoozes left" in err["error"]
    clock.t += 86400                                            # a new day
    assert d.snooze(d.sessions[b])["type"] == "ok"


def test_snooze_from_client_and_parked_session_cannot_snooze():
    d, clock, mon = make()
    b, hb, _ = join(d, "B", 102)
    d.on_message(b, {"type": "snooze"})
    assert hb.kinds()[-1] == "snooze_ok"
    park(d, b, clock, mon)
    assert d.control({"type": "snooze", "session": b})["type"] == "error"


# ---------------------------------------------------------------- queue ETA

def test_slot_free_estimates():
    assert slot_free_in(SlotView(None), 30, 10, 600) == (0.0, "free")
    assert slot_free_in(SlotView("warned", park_in_s=4), 30, 10, 600) == (6.0, "warned")
    assert slot_free_in(SlotView("idle", idle_for=20), 30, 10, 600) == (22.0, "idle")
    assert slot_free_in(SlotView("active"), 30, 10, 600) == (642.0, "guess")
    assert slot_free_in(SlotView("idle", whitelisted=True), 30, 10, 600) == (None, "unknown")
    etas = queue_etas([SlotView("active"), SlotView("warned", park_in_s=3)], 3, 30, 10, 600)
    assert etas == [(5.0, "warned"), (642.0, "guess"), (None, "unknown")]


def test_status_queue_has_eta_and_next_in_line_notification():
    d, clock, mon = make(**{"slots.count": 1})
    a, ha, _ = join(d, "A", 101)
    c, hc, _ = join(d, "C", 103)
    run(d, clock, mon, 20)                                      # A idle for 20 s
    q = d.status()["queue"]
    assert q[0]["user"] == "C" and q[0]["position"] == 1
    assert q[0]["eta_basis"] == "idle" and q[0]["eta_s"] == pytest.approx(30 - 20 + 10 + 2, abs=1.5)
    assert hc.notes().count("next") == 1                        # told once, not every tick
    park(d, a, clock, mon)
    assert "ready" in hc.notes()


# ---------------------------------------------------------------- notifications

def test_notification_kinds_and_inbox():
    d, clock, mon = make(**{"slots.count": 1})
    b, hb, _ = join(d, "B", 102)
    run(d, clock, mon, 31)
    assert "warn" in hb.notes()
    inbox = [e for e in d.events if e["kind"] == "notify"]
    assert inbox and inbox[-1]["detail"]["what"] == "warn" and "snooze" in inbox[-1]["detail"]["body"]


def test_notifier_filters_kinds_and_users_and_survives_failures():
    calls = []

    def sender(url, body, headers):
        if "broken" in url:
            raise OSError("down")
        calls.append((url, body, headers))

    n = Notifier({"kinds": ["ready"], "webhooks": [
        {"kind": "discord", "url": "http://x/discord", "users": ["alice"]},
        {"kind": "ntfy", "url": "http://x/ntfy"},
        {"kind": "slack", "url": "http://broken"}]}, sender=sender)
    assert n.wants("ready") and not n.wants("warn")
    n.push("alice", "ready", "Your GPU is ready", "slot 0")
    n.push("bob", "ready", "Your GPU is ready", "slot 1")
    n.flush()
    urls = [c[0] for c in calls]
    assert urls.count("http://x/discord") == 1 and urls.count("http://x/ntfy") == 2
    body, headers = webhook_payload("discord", "alice", "T", "B")
    assert b'"content"' in body and headers["Content-Type"] == "application/json"
    body, headers = webhook_payload("ntfy", "alice", "T", "B")
    assert body == b"B" and headers["Title"] == "T (alice)"


# ---------------------------------------------------------------- why was I parked (timeline)

def test_timeline_explains_the_park_and_survives_sid_reuse(tmp_path):
    db = tmp_path / "s.db"
    for run_no in range(2):                                     # two daemon runs both use s1, s2
        store = StateStore(db)
        d, clock, mon = make()
        d.store = store
        b, hb, _ = join(d, "B", 102)
        c, hc, _ = join(d, "C", 103)
        park(d, b, clock, mon, busy=[103])
        store.close()
    evs = read_session_events(db, b)
    kinds = [e["kind"] for e in evs]
    assert kinds.count("register") == 1                         # only the latest run's session
    assert kinds.index("idle") < kinds.index("warn") < kinds.index("park") < kinds.index("parked")
    texts = [e["text"] for e in timeline(evs)]
    assert any("Warned: no GPU work for 30 s" in t for t in texts)
    assert any(t.startswith("Parked in 0.30 s") for t in texts)
    assert describe({"kind": "something_new", "detail": {}}) == "Something new."


# ---------------------------------------------------------------- right-sizing tips

def test_memory_tips():
    gib = 2**30
    assert memory_tip(0, 2 * gib, 5) is None
    assert memory_tip(gib, 2 * gib, 0) is None                  # no steps yet: nothing to say
    t = memory_tip(int(0.5 * gib), int(2.2 * gib), 10)
    assert t["kind"] == "oversized" and t["suggested_gib"] == 0.75
    assert memory_tip(int(2.1 * gib), int(2.2 * gib), 10)["kind"] == "near_cap"
    assert memory_tip(int(1.5 * gib), int(2.2 * gib), 10) is None


def test_status_carries_peak_and_tip():
    d, clock, mon = make()
    b, hb, _ = join(d, "B", 102)
    d.on_message(b, {"type": "report", "mem_reserved": 900 * MIB, "mem_allocated": 300 * MIB,
                     "peak_allocated": 400 * MIB, "steps": 3})
    s = next(x for x in d.status()["sessions"] if x["sid"] == b)
    assert s["peak_mib"] == 400 and s["tip"]["kind"] == "oversized"


# ---------------------------------------------------------------- spill to disk

def test_long_parked_session_is_spilled_and_restore_clears_it():
    d, clock, mon = make(**{"parker.spill_after_s": 60, "slots.count": 1})
    b, hb, _ = join(d, "B", 102)
    park(d, b, clock, mon)
    run(d, clock, mon, 59)
    assert "spill" not in hb.kinds()
    run(d, clock, mon, 2)
    spill = [m for m in hb.sent if m["type"] == "spill"]
    assert len(spill) == 1 and spill[0]["dir"].endswith("spill")
    d.on_message(b, {"type": "spilled", "seconds": 1.5, "bytes": 2**30})
    assert d.sessions[b].on_disk and "spilled" in hb.notes()
    run(d, clock, mon, 100)
    assert [m["type"] for m in hb.sent].count("spill") == 1    # asked once
    d.on_message(b, {"type": "request"})
    d.on_message(b, {"type": "resumed", "restore_s": 2.0})
    assert not d.sessions[b].on_disk and d.sessions[b].state == ACTIVE


def test_spill_disabled_with_zero():
    d, clock, mon = make(**{"parker.spill_after_s": 0})
    b, hb, _ = join(d, "B", 102)
    park(d, b, clock, mon)
    run(d, clock, mon, 500)
    assert "spill" not in hb.kinds()


# ---------------------------------------------------------------- deadline mode

def test_schedule_window_and_date_parsing():
    today = date.today()
    sch = [{"name": "deadline week", "start": today - timedelta(days=1), "end": today.isoformat(),
            "idle_window_s": 600, "grace_period_s": 60}]
    assert active_schedule(sch, time.time())["name"] == "deadline week"
    assert active_schedule(sch, time.time() + 2 * 86400) is None
    fields, mode = effective_overrides(sch, None, time.time())
    assert fields == {"idle_window_s": 600.0, "grace_period_s": 60.0} and mode["source"] == "schedule"
    later = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    assert active_schedule([{"start": later, "end": later}], time.time()) is None


def test_deadline_mode_changes_the_policy_at_runtime():
    d, clock, mon = make()
    reply = d.control({"type": "set_mode", "name": "exam week", "idle_window_s": 10, "grace_period_s": 5,
                       "minutes": 2})
    assert reply["mode"]["name"] == "exam week" and d.policy.idle_window_s == 10
    b, hb, _ = join(d, "B", 102)
    run(d, clock, mon, 11)
    assert d.sessions[b].state == WARNED                        # 10 s window instead of 30
    assert d.status()["mode"]["name"] == "exam week"
    run(d, clock, mon, 120)                                     # mode expires after 2 minutes
    assert d.policy.idle_window_s == 30 and d.mode is None
    d.control({"type": "set_mode", "idle_window_s": 5})
    d.control({"type": "clear_mode"})
    assert d.policy.idle_window_s == 30
    assert [e["detail"]["name"] for e in d.events if e["kind"] == "mode"][:2] == ["exam week", "normal"]


# ---------------------------------------------------------------- impact stats

def test_user_stats_and_leaderboard(tmp_path):
    db = tmp_path / "s.db"
    store = StateStore(db)
    d, clock, mon = make(**{"slots.count": 1})
    clock.t = time.time()
    d.store = store
    b, hb, _ = join(d, "B", 102)
    c, hc, _ = join(d, "C", 103)
    park(d, b, clock, mon, busy=[103])                         # B's slot goes to C
    run(d, clock, mon, 60, busy=[103])
    d.on_message(b, {"type": "request"})                       # closes the receipt: saved C >= 60 s
    d.on_message(c, {"type": "bye"})
    d.on_message(b, {"type": "resumed", "restore_s": 0.1})
    d.snooze(d.sessions[b])
    d.on_message(b, {"type": "bye"})
    store.close()
    stats = user_stats(db, now=clock.t)
    assert stats["B"]["parks"] == 1 and stats["B"]["people_helped"] == 1
    assert stats["B"]["given_back_h"] == pytest.approx(61 / 3600, abs=2 / 3600)
    assert stats["B"]["wait_saved_for_others_h"] == pytest.approx(stats["C"]["wait_saved_for_you_h"])
    assert stats["B"]["wait_saved_for_others_h"] > 59 / 3600
    assert stats["B"]["snoozes"] == 1 and stats["C"]["avg_wait_s"] > 30
    lb = leaderboard(db, days=1, now=clock.t)
    assert lb["users"][0]["user"] == "B" and lb["totals"]["parks"] == 1


# ---------------------------------------------------------------- dry-run report

def test_dry_run_counts_virtual_parks_and_report(tmp_path):
    db = tmp_path / "s.db"
    store = StateStore(db)
    d, clock, mon = make(**{"daemon.dry_run": True, "slots.count": 1})
    clock.t = time.time()
    d.store = store
    b, hb, _ = join(d, "B", 102)
    c, hc, _ = join(d, "C", 103)                               # waits the whole time
    run(d, clock, mon, 45)
    assert any(e["kind"] == "would_park" for e in d.events)
    assert d.sessions[b].state == IDLE and d.sessions[b].dry_parked_at
    run(d, clock, mon, 100)                                    # "parked" 100 s more, no repeated would_park
    assert [e["kind"] for e in d.events].count("would_park") == 1
    run(d, clock, mon, 1, busy=[102])                          # B computes again
    assert d.sessions[b].state == ACTIVE
    assert "park" not in hb.kinds() and "warn" not in hb.kinds()
    st = d.status()["stats"]
    assert st["dry_would_reclaim_s"] == pytest.approx(106, abs=2)     # would_park at 40 s, active at 146 s
    assert st["dry_saved_wait_s"] == pytest.approx(106, abs=2)        # C was waiting all along
    store.save_snapshot(clock.t, d.status())
    store.close()
    r = dryrun_report(db, now=clock.t + 100)                   # daemon "stopped": open parks use snapshot time
    assert r["would_parks"] == 1 and r["per_user"][0]["user"] == "B"
    assert r["would_reclaim_gpu_h"] == pytest.approx(106 / 3600, abs=2 / 3600)
    assert "Dry-run report" in r["text"] and "| B | 1 |" in r["text"]
