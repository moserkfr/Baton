"""Discrete-event simulation of a small GPU lab under one reclaim policy.

The lab is a pool of identical GPUs (machines x GPUs per machine; which machine a GPU sits on does not
change any metric, so the pool is flat). Sessions arrive, wait in a fair queue for a GPU, then run their
program (see workload.py). The policy decides what happens during idle gaps.

Event flow for one notebook session:

    arrive -> request -> (queue) -> grant -> segment, segment, ... -> finish
                                   ^                 |
                                   |   idle timers fire during gaps: warn / park / kill
                                   |                 v
                                   +-- request again when the user's next TASK starts
                                       (park: restore, state kept; kill: re-run lost work first)

Times are minutes. Stale timers are ignored with an epoch counter: every time a session's activity
changes, its epoch increments and older timers no longer match.
"""
from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .policies import Policy
from .workload import GAP, TASK, Session


@dataclass
class QueueParams:
    usage_halflife_min: float = 24 * 60.0   # recent GPU usage decays with this half-life
    usage_weight: float = 10.0              # minutes of wait credit lost per recent GPU-hour
    returning_priority_min: float = 2.0     # parked owners jump the queue for this long after asking


@dataclass
class SessionRun:
    """Mutable per-session simulation state and the per-session results."""
    s: Session
    idx: int = -1
    status: str = "new"         # new | waiting | running | parking | parked | killed | done
    has_gpu: bool = False
    hold_start: float = 0.0
    held: float = 0.0           # GPU-minutes held
    busy: float = 0.0           # GPU-minutes computing (tasks + re-runs)
    rerun: float = 0.0          # GPU-minutes spent re-running work lost to a kill
    done_task_time: float = 0.0  # original task minutes completed so far (what a kill destroys)
    epoch: int = 0
    pending_task: bool = False  # a task started while a park was in progress
    req_time: float = 0.0
    req_kind: str = "first"     # first | park_return | kill_return
    warns: int = 0
    parks: int = 0
    kills: int = 0
    warned_then_resumed: int = 0
    _warned: bool = False
    finish_time: float = 0.0


@dataclass
class SimResult:
    policy: Policy
    runs: list[SessionRun]
    waits: list[tuple[str, float]]   # (request kind, minutes waited incl. restore)
    n_gpus: int
    makespan: float
    log: list[tuple[float, str, int]] = field(default_factory=list)


class LabSim:
    def __init__(self, sessions: list[Session], policy: Policy, n_gpus: int,
                 queue: QueueParams | None = None, keep_log: bool = False):
        self.policy = policy
        self.n_gpus = n_gpus
        self.free = n_gpus
        self.qp = queue or QueueParams()
        self.now = 0.0
        self._heap: list[tuple[float, int, Callable, tuple]] = []
        self._seq = itertools.count()
        self.runs = [SessionRun(s) for s in sessions]
        self.queue: list[SessionRun] = []
        self.waits: list[tuple[str, float]] = []
        self._usage: dict[int, tuple[float, float]] = {}  # user -> (decayed GPU-hours, as of time)
        self.keep_log = keep_log
        self.log: list[tuple[float, str, int]] = []

    # ------------------------------------------------------------ event plumbing
    def at(self, t: float, fn: Callable, *args) -> None:
        heapq.heappush(self._heap, (t, next(self._seq), fn, args))

    def note(self, what: str, r: SessionRun) -> None:
        if self.keep_log:
            self.log.append((self.now, what, r.s.sid))

    def run(self) -> SimResult:
        for r in self.runs:
            self.at(r.s.arrival, self._arrive, r)
        while self._heap:
            self.now, _, fn, args = heapq.heappop(self._heap)
            fn(*args)
        return SimResult(self.policy, self.runs, self.waits, self.n_gpus, self.now, self.log)

    # ------------------------------------------------------------ fair queue
    def _recent_usage(self, user: int) -> float:
        value, t = self._usage.get(user, (0.0, self.now))
        return value * 0.5 ** ((self.now - t) / self.qp.usage_halflife_min)

    def _add_usage(self, user: int, minutes: float) -> None:
        self._usage[user] = (self._recent_usage(user) + minutes / 60.0, self.now)

    def _score(self, r: SessionRun) -> float:
        """Higher is served first: waiting time minus a penalty for recent GPU-hours used."""
        wait = self.now - r.req_time
        score = wait - self.qp.usage_weight * self._recent_usage(r.s.user)
        if (self.policy.returning_priority and r.req_kind == "park_return"
                and wait <= self.qp.returning_priority_min):
            score += 1e9
        return score

    def _request(self, r: SessionRun, kind: str) -> None:
        r.req_time, r.req_kind, r.status = self.now, kind, "waiting"
        self.queue.append(r)
        self.note(f"request:{kind}", r)
        self._dispatch()

    def _dispatch(self) -> None:
        while self.free > 0 and self.queue:
            best = max(self.queue, key=self._score)
            self.queue.remove(best)
            self._grant(best)

    def _grant(self, r: SessionRun) -> None:
        self.free -= 1
        r.has_gpu, r.hold_start, r.status = True, self.now, "running"
        wait = self.now - r.req_time
        self.note("grant", r)
        if r.req_kind == "first":
            self.waits.append(("first", wait))
            self._advance(r)
        elif r.req_kind == "park_return":
            restore = self.policy.restore_s / 60.0
            self.waits.append(("park_return", wait + restore))
            self.at(self.now + restore, self._start_task, r, 0.0)
        else:  # kill_return: the user re-runs everything the kill destroyed before continuing
            self.waits.append(("kill_return", wait))
            self._start_task(r, r.done_task_time)

    def _release(self, r: SessionRun) -> None:
        held = self.now - r.hold_start
        r.held += held
        r.has_gpu = False
        self._add_usage(r.s.user, held)
        self.free += 1
        self._dispatch()

    # ------------------------------------------------------------ session program
    def _arrive(self, r: SessionRun) -> None:
        self._request(r, "first")

    def _advance(self, r: SessionRun) -> None:
        r.idx += 1
        if r.idx == len(r.s.program):
            self._finish(r)
            return
        seg = r.s.program[r.idx]
        if seg.kind == TASK:
            if r.status == "parking":
                r.pending_task = True
            elif r.has_gpu:
                self._start_task(r, 0.0)
            else:
                self._request(r, "park_return" if r.status == "parked" else "kill_return")
        else:
            self.at(self.now + seg.dur, self._segment_done, r, r.idx)
            self._gap_started(r, seg.attended)

    def _start_task(self, r: SessionRun, extra: float) -> None:
        r.epoch += 1  # GPU work cancels every pending idle timer
        if r._warned:  # the user came back between the warning and the park/kill
            r._warned = False
            r.warned_then_resumed += 1
        r.status = "running"
        dur = r.s.program[r.idx].dur + extra
        r.busy += dur
        r.rerun += extra
        self.at(self.now + dur, self._segment_done, r, r.idx)

    def _segment_done(self, r: SessionRun, idx: int) -> None:
        seg = r.s.program[idx]
        if seg.kind == TASK:
            r.done_task_time += seg.dur
        self._advance(r)

    def _finish(self, r: SessionRun) -> None:
        r.epoch += 1
        if r.has_gpu:
            self._release(r)
        r.status = "done"
        r.finish_time = self.now
        self.note("finish", r)

    # ------------------------------------------------------------ idle policies
    def _gap_started(self, r: SessionRun, attended: bool) -> None:
        if not r.has_gpu:
            return
        r.epoch += 1
        ep, p = r.epoch, self.policy
        if p.kind == "culler" and not attended:
            self.at(self.now + p.idle_window, self._kill, r, ep)
        elif p.kind == "gpu_cancel":
            self.at(self.now + p.warn_after, self._warn, r, ep)
            self.at(self.now + p.idle_window, self._kill, r, ep)
        elif p.kind == "park":
            self.at(self.now + p.idle_window, self._warn, r, ep)
            self.at(self.now + p.idle_window + p.grace, self._park, r, ep)

    def _alive(self, r: SessionRun, ep: int) -> bool:
        return ep == r.epoch and r.has_gpu and r.status == "running"

    def _warn(self, r: SessionRun, ep: int) -> None:
        if self._alive(r, ep):
            r.warns += 1
            r._warned = True  # cleared by the park/kill, or counted as resumed by _start_task
            self.note("warn", r)

    def _kill(self, r: SessionRun, ep: int) -> None:
        if self._alive(r, ep):
            r.kills += 1
            r.status = "killed"
            r._warned = False
            self.note("kill", r)
            self._release(r)

    def _park(self, r: SessionRun, ep: int) -> None:
        if self._alive(r, ep):
            r.status = "parking"
            r._warned = False
            self.note("park", r)
            self.at(self.now + self.policy.park_s / 60.0, self._park_done, r)

    def _park_done(self, r: SessionRun) -> None:
        if r.status != "parking":  # the session logged out mid-park; _finish already freed the GPU
            return
        r.parks += 1
        self._release(r)
        r.status = "parked"
        if r.pending_task:
            r.pending_task = False
            self._request(r, "park_return")


def simulate(sessions: list[Session], policy: Policy, n_gpus: int,
             queue: QueueParams | None = None, keep_log: bool = False) -> SimResult:
    """Run one policy over a workload. Sessions are not mutated, so the same list can be reused."""
    return LabSim(sessions, policy, n_gpus, queue, keep_log).run()


# ---------------------------------------------------------------- metrics

def jain_index(values: list[float] | np.ndarray) -> float:
    """Jain's fairness index: 1.0 = perfectly equal, 1/n = one user gets everything."""
    x = np.asarray(values, dtype=float)
    if len(x) == 0 or not np.any(x):
        return 1.0
    return float(x.sum() ** 2 / (len(x) * (x ** 2).sum()))


def metrics(res: SimResult, n_users: int) -> dict[str, float]:
    """Headline metrics for one simulation run."""
    held = sum(r.held for r in res.runs) / 60.0
    busy = sum(r.busy for r in res.runs) / 60.0
    rerun = sum(r.rerun for r in res.runs) / 60.0
    waits = np.array([w for _, w in res.waits])
    returns = np.array([w for k, w in res.waits if k != "first"])
    per_user = np.zeros(n_users)
    for r in res.runs:
        per_user[r.s.user] += r.held
    notebooks = [r for r in res.runs if not r.s.batch]
    return {
        "gpu_hours_held": held,
        "idle_gpu_hours_held": held - busy,
        "gpu_hours_computing": busy,
        "rerun_gpu_hours": rerun,
        "wait_median_min": float(np.median(waits)) if len(waits) else 0.0,
        "wait_p95_min": float(np.percentile(waits, 95)) if len(waits) else 0.0,
        "wait_mean_min": float(waits.mean()) if len(waits) else 0.0,
        "returns": float(len(returns)),
        "return_wait_p95_min": float(np.percentile(returns, 95)) if len(returns) else 0.0,
        "sessions": float(len(notebooks)),
        "sessions_state_lost": float(sum(1 for r in notebooks if r.kills > 0)),
        "users_returned_to_lost_state": float(sum(1 for k, _ in res.waits if k == "kill_return")),
        "parks": float(sum(r.parks for r in res.runs)),
        "warns": float(sum(r.warns for r in res.runs)),
        "warned_then_resumed": float(sum(r.warned_then_resumed for r in res.runs)),
        "jain_gpu_hours": jain_index(per_user),
        "utilization_pct": 100.0 * busy / (res.n_gpus * res.makespan / 60.0) if res.makespan else 0.0,
    }
