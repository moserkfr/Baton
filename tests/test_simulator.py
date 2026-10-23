"""Unit tests for the simulator's event logic, using tiny hand-built sessions with known answers."""
import numpy as np
import pytest

from baton.simulator.policies import (activity_culler, gpu_aware_cancel, hold_until_logout,
                                               park_and_restore)
from baton.simulator.sim import LabSim, QueueParams, jain_index, metrics
from baton.simulator.workload import (GAP, TASK, Segment, Session, WorkloadParams,
                                               calibration_report, generate_workload)

PARK = park_and_restore(30, 5, park_s=1.2, restore_s=0.27)


def sess(sid, user, arrival, *segs, batch=False):
    return Session(sid, user, arrival, [Segment(*s) for s in segs], batch=batch)


# setup 1 min, task 2, unattended break 60, task 3, short tail 5
WORK_BREAK_WORK = [(GAP, 1, True), (TASK, 2), (GAP, 60, False), (TASK, 3), (GAP, 5, False)]


def run(sessions, policy, n_gpus=1, qp=None):
    sim = LabSim(sessions, policy, n_gpus, qp)
    res = sim.run()
    assert sim.free == n_gpus, "every GPU must be released at the end"
    assert not sim.queue
    return res


def test_hold_keeps_gpu_for_whole_lifetime():
    res = run([sess(0, 0, 0, *WORK_BREAK_WORK)], hold_until_logout())
    r = res.runs[0]
    assert r.held == pytest.approx(71)
    assert r.busy == pytest.approx(5)
    assert r.parks == r.kills == 0


def test_park_frees_gpu_and_restores_without_losing_state():
    res = run([sess(0, 0, 0, *WORK_BREAK_WORK)], PARK)
    r = res.runs[0]
    assert r.parks == 1 and r.warns == 1 and r.kills == 0
    # held: 0..(3+30+5+park) then (63..end incl. restore 0.27 s)
    park_min, restore_min = 1.2 / 60, 0.27 / 60
    assert r.held == pytest.approx(38 + park_min + restore_min + 3 + 5)
    kinds = [k for k, _ in res.waits]
    assert kinds == ["first", "park_return"]
    assert res.waits[1][1] == pytest.approx(restore_min)  # returning user only feels the restore
    assert r.rerun == 0


def test_culler_kills_in_unattended_gap_and_user_reruns_lost_work():
    res = run([sess(0, 0, 0, *WORK_BREAK_WORK)], activity_culler(30))
    r = res.runs[0]
    assert r.kills == 1
    assert r.rerun == pytest.approx(2)        # the 2-minute task before the break is re-run
    assert r.busy == pytest.approx(5 + 2)
    assert [k for k, _ in res.waits] == ["first", "kill_return"]


def test_culler_ignores_attended_gaps():
    s = sess(0, 0, 0, (GAP, 1, True), (TASK, 2), (GAP, 60, True), (TASK, 3), (GAP, 1, True))
    assert run([s], activity_culler(30)).runs[0].kills == 0


def test_park_policy_sees_gpu_idleness_even_when_user_is_typing():
    s = sess(0, 0, 0, (GAP, 1, True), (TASK, 2), (GAP, 60, True), (TASK, 3), (GAP, 1, True))
    assert run([s], PARK).runs[0].parks == 1


def test_gpu_cancel_warns_but_user_returns_before_cancel():
    res = run([sess(0, 0, 0, *WORK_BREAK_WORK)], gpu_aware_cancel(45, 90))
    r = res.runs[0]
    assert r.warns == 1 and r.kills == 0 and r.warned_then_resumed == 1


def test_park_cancelled_if_user_returns_during_grace():
    s = sess(0, 0, 0, (GAP, 1, True), (TASK, 2), (GAP, 32, False), (TASK, 3), (GAP, 1, True))
    r = run([s], PARK).runs[0]
    assert r.warns == 1 and r.parks == 0 and r.warned_then_resumed == 1


def test_session_logging_out_mid_park_releases_gpu_once():
    tail = 30 + 5 + 0.01  # ends 0.6 s into the 1.2 s park
    run([sess(0, 0, 0, (GAP, 1, True), (TASK, 2), (GAP, tail, False))], PARK)  # run() checks free == n


def test_parked_gpu_goes_to_queued_user():
    hog = sess(0, 0, 0, (GAP, 1, True), (TASK, 2), (GAP, 300, False))
    waiter = sess(1, 1, 10, (GAP, 1, True), (TASK, 5), (GAP, 1, True))
    res_hold = run([hog, waiter], hold_until_logout())
    res_park = run([hog, waiter], PARK)
    wait_hold = res_hold.waits[1][1]
    wait_park = res_park.waits[1][1]
    assert wait_hold == pytest.approx(303 - 10)
    assert wait_park == pytest.approx(38 + 1.2 / 60 - 10)


def test_returning_owner_has_priority_over_queue():
    owner = sess(0, 0, 0, *WORK_BREAK_WORK)
    # occupies the second GPU until long after the owner returns at t=63
    other = sess(1, 1, 0, (GAP, 1, True), (TASK, 200), (GAP, 1, True))
    # arrives after the park and holds the freed GPU; then a third user queues before the owner returns
    taker = sess(2, 2, 40, (GAP, 1, True), (TASK, 30), (GAP, 1, True))   # frees at 72
    late = sess(3, 3, 50, (GAP, 1, True), (TASK, 5), (GAP, 1, True))     # queued since 50
    res = run([owner, other, taker, late], PARK, n_gpus=2)
    # owner asked at 63 (13 min after `late`), but returning priority only lasts 2 min, so `late` wins
    owner_wait = [w for k, w in res.waits if k == "park_return"][0]
    assert owner_wait > 1  # waited behind `late`
    # with a long priority window the owner goes first
    res2 = run([owner, other, taker, late], PARK, n_gpus=2, qp=QueueParams(returning_priority_min=60))
    owner_wait2 = [w for k, w in res2.waits if k == "park_return"][0]
    assert owner_wait2 < owner_wait


def test_fair_share_serves_light_user_first():
    heavy_past = sess(0, 0, 0, (TASK, 600))                          # user 0 used 10 GPU-hours
    blocker = sess(1, 2, 590, (TASK, 20))                            # holds the GPU 610..630
    heavy_now = sess(2, 0, 615, (TASK, 5))                           # user 0 asks first
    light_now = sess(3, 1, 620, (TASK, 5))                           # user 1 asks 5 min later
    res = run([heavy_past, blocker, heavy_now, light_now], hold_until_logout(),
              qp=QueueParams(usage_weight=10))
    by_sid = {r.s.sid: r for r in res.runs}
    assert by_sid[3].finish_time < by_sid[2].finish_time


def test_batch_jobs_are_never_parked():
    job = sess(0, 0, 0, (TASK, 300), batch=True)
    for pol in (PARK, activity_culler(10), gpu_aware_cancel()):
        r = run([job], pol).runs[0]
        assert r.parks == r.kills == r.warns == 0


def test_jain_index():
    assert jain_index([1, 1, 1, 1]) == pytest.approx(1.0)
    assert jain_index([1, 0, 0, 0]) == pytest.approx(0.25)
    assert jain_index([]) == 1.0


def test_workload_is_reproducible():
    p = WorkloadParams(days=2)
    a, b = generate_workload(7, p), generate_workload(7, p)
    assert [(s.arrival, s.lifetime) for s in a] == [(s.arrival, s.lifetime) for s in b]
    c = generate_workload(8, p)
    assert [s.arrival for s in a] != [s.arrival for s in c]


def test_calibration_close_to_notebookos():
    rows = {r.name: r.achieved for r in calibration_report(generate_workload(1, WorkloadParams(days=200)))}
    assert rows["median task length"] == pytest.approx(2.0, abs=0.3)
    assert rows["median gap between tasks"] == pytest.approx(5.0, abs=0.5)
    assert 70 <= rows["tasks shorter than 5 min"] <= 82
    assert 70 <= rows["gaps shorter than 8 min"] <= 82
    assert 70 <= rows["sessions GPU-active <= 5% of lifetime"] <= 79
    assert 86 <= rows["sessions GPU-active <= 31% of lifetime"] <= 94


def test_park_policy_never_loses_state_on_full_workload():
    p = WorkloadParams(days=2)
    ss = generate_workload(3, p)
    m = metrics(LabSim(ss, PARK, 8).run(), p.students)
    assert m["sessions_state_lost"] == 0 and m["rerun_gpu_hours"] == 0
    m_hold = metrics(LabSim(ss, hold_until_logout(), 8).run(), p.students)
    assert m["idle_gpu_hours_held"] < m_hold["idle_gpu_hours_held"]
    assert m["gpu_hours_computing"] == pytest.approx(m_hold["gpu_hours_computing"])
