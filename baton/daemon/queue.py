"""Fair queue for free slots.

Order: whoever has waited longest goes first, minus a penalty for GPU-hours used recently (classic
fair share, as Slurm and KAI Scheduler do), so a student who just had the GPU for hours doesn't beat
someone who has had nothing. Recent usage decays with a half-life (24 h by default).

A parked owner who comes back jumps the queue for a short window (`returning_priority_s`): their notebook
is already set up, and a two-second restore is the whole point of parking. After the window they
compete like everyone else, so a returning owner can't block the queue forever.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class QueueEntry:
    sid: str
    user: str
    since: float
    returning: bool = False


class UsageTracker:
    """Exponentially decayed GPU-hours per user."""

    def __init__(self, halflife_h: float = 24.0):
        self.halflife_s = halflife_h * 3600.0
        self._v: dict[str, tuple[float, float]] = {}

    def get(self, user: str, now: float) -> float:
        value, t = self._v.get(user, (0.0, now))
        return value * 0.5 ** ((now - t) / self.halflife_s)

    def add(self, user: str, seconds: float, now: float) -> None:
        self._v[user] = (self.get(user, now) + seconds / 3600.0, now)


class FairQueue:
    def __init__(self, usage: UsageTracker, usage_weight_s_per_gpuh: float = 600.0,
                 returning_priority_s: float = 120.0):
        self.usage = usage
        self.weight = usage_weight_s_per_gpuh
        self.returning_priority_s = returning_priority_s
        self.entries: list[QueueEntry] = []

    def push(self, entry: QueueEntry) -> None:
        self.remove(entry.sid)
        self.entries.append(entry)

    def remove(self, sid: str) -> None:
        self.entries = [e for e in self.entries if e.sid != sid]

    def __contains__(self, sid: str) -> bool:
        return any(e.sid == sid for e in self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def score(self, e: QueueEntry, now: float) -> float:
        wait = now - e.since
        s = wait - self.weight * self.usage.get(e.user, now)
        if e.returning and wait <= self.returning_priority_s:
            s += 1e9
        return s

    def ordered(self, now: float) -> list[QueueEntry]:
        return sorted(self.entries, key=lambda e: self.score(e, now), reverse=True)

    def pop(self, now: float) -> QueueEntry | None:
        if not self.entries:
            return None
        best = self.ordered(now)[0]
        self.remove(best.sid)
        return best
