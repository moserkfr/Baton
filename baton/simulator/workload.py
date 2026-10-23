"""Workload generation for the lab simulator.

Two kinds of GPU demand:

* **Notebook sessions** (synthetic). Each session is a *program*: a list of segments run one after
  another once the session gets a GPU. A segment is either a TASK (GPU busy) or a GAP (GPU idle).
  Gaps are *attended* (the user is at the notebook editing code, so web activity exists) or
  *unattended* (lunch, a meeting, gone home: no web activity). The generator is calibrated to the
  NotebookOS (ASPLOS 2026) statistics; `calibration_report` measures how close it gets.
* **Batch jobs** (sampled from the SenseTime Helios trace, Venus cluster, 1-GPU jobs). They keep the
  GPU busy from start to end, so no idle policy ever touches them; they add realistic background load.

All times are in minutes. Everything is driven by one seeded numpy Generator, so a given seed always
produces the same workload.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

TASK, GAP = "task", "gap"
DAY = 24 * 60.0


@dataclass(frozen=True)
class Segment:
    kind: str            # TASK or GAP
    dur: float           # minutes
    attended: bool = True  # GAP only: is the user at the notebook (web activity)?


@dataclass
class Session:
    sid: int
    user: int
    arrival: float                 # minute the user asks for a GPU
    program: list[Segment]
    batch: bool = False            # batch job: one TASK, never idle

    @property
    def lifetime(self) -> float:
        return sum(s.dur for s in self.program)

    @property
    def task_time(self) -> float:
        return sum(s.dur for s in self.program if s.kind == TASK)

    @property
    def active_fraction(self) -> float:
        return self.task_time / self.lifetime if self.lifetime > 0 else 0.0


@dataclass
class WorkloadParams:
    """Knobs of the synthetic generator. Defaults were tuned with `calibration_report` (see docs)."""
    students: int = 40
    days: int = 5
    sessions_per_student_day: float = 0.55   # mean; each student's own rate is gamma-distributed
    student_rate_shape: float = 2.0          # gamma shape: lower = more unequal students
    p_never_active: float = 0.66             # session opens, loads to GPU, never trains (NotebookOS: ~70%)
    task_median: float = 2.1                 # NotebookOS: median task 2 min
    task_sigma: float = 1.25                 # -> P75 ~ 4.6 min (< 5 min)
    gap_median: float = 4.85                 # NotebookOS: median gap between tasks 5 min
    gap_sigma: float = 0.60                  # -> P75 < 8 min once breaks are mixed in
    tasks_per_burst_mean: float = 6.0        # tasks in one sitting (geometric, >= 1)
    extra_bursts_mean: float = 0.4           # additional sittings after a break (Poisson)
    break_median: float = 50.0               # unattended break between sittings
    break_sigma: float = 0.6
    setup_median: float = 3.0                # open notebook, load data/model before first task
    tail_median: float = 25.0                # active sessions: idle tail before logout
    tail_sigma: float = 1.5
    idle_life_median: float = 150.0          # never-active sessions: how long they stay open
    idle_life_sigma: float = 0.9
    p_overnight: float = 0.12                # session left open overnight (tail of ~14 h)
    workday_start: float = 9 * 60.0
    workday_end: float = 18 * 60.0
    batch_jobs_per_day: float = 12.0
    batch_max_minutes: float = 12 * 60.0     # lab-scale cap on Helios job lengths


def _lognormal(rng: np.random.Generator, median: float, sigma: float) -> float:
    return float(median * np.exp(sigma * rng.standard_normal()))


def make_session_program(rng: np.random.Generator, p: WorkloadParams) -> list[Segment]:
    """Generate one notebook session's segment list."""
    prog = [Segment(GAP, _lognormal(rng, p.setup_median, 0.5), attended=True)]
    overnight = rng.random() < p.p_overnight
    if rng.random() < p.p_never_active:
        tail = _lognormal(rng, p.idle_life_median, p.idle_life_sigma)
    else:
        bursts = 1 + rng.poisson(p.extra_bursts_mean)
        for b in range(bursts):
            if b > 0:
                prog.append(Segment(GAP, _lognormal(rng, p.break_median, p.break_sigma), attended=False))
            n_tasks = rng.geometric(1.0 / p.tasks_per_burst_mean)
            for i in range(n_tasks):
                if i > 0:
                    prog.append(Segment(GAP, _lognormal(rng, p.gap_median, p.gap_sigma), attended=True))
                prog.append(Segment(TASK, max(0.1, _lognormal(rng, p.task_median, p.task_sigma))))
        tail = _lognormal(rng, p.tail_median, p.tail_sigma)
    if overnight:
        tail += 14 * 60.0
    prog.append(Segment(GAP, max(1.0, tail), attended=False))
    return prog


def generate_notebook_sessions(rng: np.random.Generator, p: WorkloadParams) -> list[Session]:
    """Sessions for every student and day, arriving during working hours."""
    rates = rng.gamma(p.student_rate_shape, p.sessions_per_student_day / p.student_rate_shape, p.students)
    sessions: list[Session] = []
    for day in range(p.days):
        for user in range(p.students):
            for _ in range(rng.poisson(rates[user])):
                # arrivals peak late morning / early afternoon (triangular over the workday)
                t = rng.triangular(p.workday_start, (p.workday_start + p.workday_end) / 2 - 60, p.workday_end - 60)
                sessions.append(Session(0, user, day * DAY + t, make_session_program(rng, p)))
    return sessions


# ---------------------------------------------------------------- Helios batch jobs

def helios_path(data_dir: Path, cluster: str = "Venus") -> Path:
    return Path(data_dir) / "helios" / "data" / cluster / "cluster_log.csv"


def load_helios_jobs(csv_path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Return (durations_min, submit_minute_of_day) for 1-GPU jobs that ran at least a minute."""
    import pandas as pd

    df = pd.read_csv(csv_path, usecols=["gpu_num", "submit_time", "duration"])
    df = df[(df.gpu_num == 1) & (df.duration >= 60)]
    submit = pd.to_datetime(df.submit_time)
    tod = (submit.dt.hour * 60 + submit.dt.minute).to_numpy(dtype=float)
    return df.duration.to_numpy(dtype=float) / 60.0, tod


def generate_batch_jobs(rng: np.random.Generator, p: WorkloadParams,
                        helios: tuple[np.ndarray, np.ndarray] | None) -> list[Session]:
    """Batch jobs sampled from Helios (or a lognormal fallback if the trace is missing)."""
    jobs: list[Session] = []
    for day in range(p.days):
        for _ in range(rng.poisson(p.batch_jobs_per_day)):
            if helios is not None:
                i = rng.integers(len(helios[0]))
                dur, tod = helios[0][i], helios[1][i]
            else:
                dur, tod = _lognormal(rng, 30.0, 1.5), rng.uniform(p.workday_start, p.workday_end)
            jobs.append(Session(0, int(rng.integers(p.students)), day * DAY + tod,
                                [Segment(TASK, float(min(dur, p.batch_max_minutes)))], batch=True))
    return jobs


def generate_workload(seed: int, p: WorkloadParams, helios_csv: Path | None = None) -> list[Session]:
    """The full, seeded workload: notebook sessions + batch jobs, sorted by arrival, ids assigned."""
    rng = np.random.default_rng(seed)
    sessions = generate_notebook_sessions(rng, p)
    helios = load_helios_jobs(helios_csv) if helios_csv and Path(helios_csv).exists() else None
    sessions += generate_batch_jobs(rng, p, helios)
    sessions.sort(key=lambda s: s.arrival)
    for i, s in enumerate(sessions):
        s.sid = i
    return sessions


# ---------------------------------------------------------------- calibration

@dataclass
class CalibrationRow:
    name: str
    target: str
    achieved: float
    unit: str = ""


def calibration_report(sessions: list[Session]) -> list[CalibrationRow]:
    """Compare generated notebook sessions with the NotebookOS published statistics."""
    nb = [s for s in sessions if not s.batch]
    tasks = np.array([g.dur for s in nb for g in s.program if g.kind == TASK])
    gaps = []  # every idle gap between two tasks of the same session (sittings + breaks)
    for s in nb:
        idx = [i for i, g in enumerate(s.program) if g.kind == TASK]
        for a, b in zip(idx, idx[1:]):
            gaps.append(sum(g.dur for g in s.program[a + 1:b]))
    gaps = np.array(gaps)
    af = np.array([s.active_fraction for s in nb])
    return [
        CalibrationRow("median task length", "2 min", float(np.median(tasks)), "min"),
        CalibrationRow("tasks shorter than 5 min", "75%", float((tasks < 5).mean() * 100), "%"),
        CalibrationRow("median gap between tasks", "5 min", float(np.median(gaps)), "min"),
        CalibrationRow("gaps shorter than 8 min", "75%", float((gaps < 8).mean() * 100), "%"),
        CalibrationRow("sessions GPU-active <= 5% of lifetime", "74-75%", float((af <= 0.05).mean() * 100), "%"),
        CalibrationRow("sessions GPU-active <= 31% of lifetime", "90%", float((af <= 0.31).mean() * 100), "%"),
        CalibrationRow("sessions never GPU-active", "~70% (\"nearly 70%\")", float((af == 0).mean() * 100), "%"),
    ]
