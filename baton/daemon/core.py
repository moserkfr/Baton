"""The daemon's brain: sessions, slots, the fair queue, and the park/restore lifecycle.

Everything here is plain method calls on one object, guarded by one lock, with an injectable clock,
monitor and parker. The TCP server (server.py) only turns socket lines into these calls, which is why
the whole lifecycle can be unit-tested with fake time and no GPU (tests/test_daemon.py).
"""
from __future__ import annotations

import dataclasses
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import resolve_path
from ..parkers.base import Parker
from .eta import SlotView, queue_etas
from .notify import Notifier
from .policy import (ACTIVE, HOLDING_SLOT, IDLE, PARKED, PARKING, QUEUED, RESTORING, WARNED,
                     PolicyConfig, decide, is_computing)
from .queue import FairQueue, QueueEntry, UsageTracker
from .schedule import effective_overrides
from .slots import SlotTable
from .tips import memory_tip


def _fmt_wait(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    return f"{seconds:.0f} s" if seconds < 90 else f"{seconds / 60:.0f} min"


@dataclass
class Session:
    sid: str
    user: str
    name: str
    pid: int
    handle: Any                       # object with .send(msg); None for sessions without a client
    whitelist: bool = False
    state: str = QUEUED
    slot: int | None = None
    registered_at: float = 0.0
    last_report: float = 0.0
    last_active: float = 0.0
    warned_at: float = 0.0
    park_started: float = 0.0
    parked_at: float = 0.0
    request_since: float = 0.0
    queued_since: float = 0.0
    slot_since: float = 0.0
    mem_reserved: int = 0
    steps: int = 0
    steps_at_tick: int = 0
    util: int = 0
    parks: int = 0
    restores: int = 0
    gpu_seconds: float = 0.0
    parked_seconds: float = 0.0
    pending_request: bool = False
    last_park_s: float = 0.0
    last_restore_s: float = 0.0
    mem_allocated: int = 0
    peak_allocated: int = 0           # PyTorch's peak allocation, for right-sizing tips
    snooze_until: float = 0.0         # no warning before this (see Daemon._idle_for)
    snoozes: int = 0
    back_at: float | None = None      # "back at 3pm": pre-restore shortly before this
    on_disk: bool = False             # parked state spilled from RAM to disk
    spill_requested: bool = False
    dry_parked_at: float = 0.0        # dry run: when we *would* have parked (0 = not)
    notified_next: bool = False       # "you're next in line" already sent

    @property
    def has_client(self) -> bool:
        return self.handle is not None

    def send(self, msg: dict[str, Any]) -> None:
        if self.handle is not None:
            try:
                self.handle.send(msg)
            except OSError:
                pass  # the reader thread will notice the disconnect


@dataclass
class Receipt:
    """'Saved you a wait': a slot freed by parking `owner` went to `beneficiary`."""
    beneficiary: str
    owner: str
    owner_sid: str
    granted_at: float
    waited_s: float
    closed_at: float | None = None

    def saved_s(self, now: float) -> float:
        """Lower bound on the extra wait without parking: until the owner came back (or now)."""
        return (self.closed_at or now) - self.granted_at


class Daemon:
    def __init__(self, cfg: dict[str, Any], monitor, parker: Parker, store=None,
                 clock: Callable[[], float] = time.time, echo: bool = False):
        self.cfg = cfg
        self.base_policy = PolicyConfig.from_config(cfg)
        self.policy = self.base_policy
        p = cfg["policy"]
        self.schedules = list(p.get("schedules") or [])
        self.runtime_mode: dict[str, Any] | None = None   # set with the set_mode control message
        self.mode: dict[str, Any] | None = None
        self.snooze_s = float(p.get("snooze_s", 900))
        self.snoozes_per_day = int(p.get("snoozes_per_day", 3))
        self.away_prewarm_s = float(p.get("away_prewarm_s", 60))
        self._snooze_count: dict[str, tuple[str, int]] = {}   # user -> (local date, snoozes used)
        self.monitor, self.parker, self.store, self.clock, self.echo = monitor, parker, store, clock, echo
        self.dry_run = bool(cfg["daemon"].get("dry_run", False))
        self.report_timeout_s = float(cfg["daemon"].get("report_timeout_s", 15))
        self.park_timeout_s = float(cfg["parker"].get("park_timeout_s", 20))
        self.spill_after_s = float(cfg["parker"].get("spill_after_s", 0) or 0)
        self.spill_dir = str(resolve_path(cfg["parker"].get("spill_dir", "data/spill")))
        self.eta_active_guess_s = float(cfg["queue"].get("eta_active_guess_s", 600))
        ncfg = cfg.get("notify") or {}
        self.inbox = bool(ncfg.get("inbox", True))
        self.notifier = Notifier(ncfg, log=lambda text: self._event("notify_error", None, error=text))
        self.slots = SlotTable(cfg["slots"]["count"], cfg["slots"]["cap_gib"], monitor.total_bytes())
        q = cfg["queue"]
        self.usage = UsageTracker(q["usage_halflife_h"])
        self.queue = FairQueue(self.usage, q["usage_weight_s_per_gpuh"], q["returning_priority_s"])
        self.sessions: dict[str, Session] = {}
        self.events: deque[dict[str, Any]] = deque(maxlen=300)
        self.receipts: list[Receipt] = []
        self._open_receipt: dict[str, Receipt] = {}      # owner sid -> receipt while owner is parked
        self._freed_by_park: dict[int, str] = {}          # slot -> owner sid whose park freed it
        self.lock = threading.RLock()
        self._next_id = 1
        self.started_at = clock()
        self.reclaimed_closed_s = 0.0                     # parked time of sessions already restored/gone
        self.last_sample = None
        self.total_parks = 0
        self.total_restores = 0
        self._last_tick: float | None = None
        self.dry_would_reclaim_s = 0.0    # dry run: GPU time that parking would have freed
        self.dry_saved_wait_s = 0.0       # dry run: queue waiting that those slots could have served

    # ------------------------------------------------------------ events
    def _event(self, kind: str, s: Session | None = None, **detail: Any) -> None:
        ev = {"ts": self.clock(), "kind": kind, "sid": s.sid if s else None,
              "user": s.user if s else None, "detail": detail}
        self.events.append(ev)
        if self.store:
            self.store.event(ev["ts"], kind, ev["sid"], ev["user"], detail)
        if self.echo:
            who = f" {s.user}({s.sid})" if s else ""
            extra = " ".join(f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}" for k, v in detail.items())
            print(f"[daemon {time.strftime('%H:%M:%S', time.localtime(ev['ts']))}] {kind}{who} {extra}", flush=True)

    def _notify(self, s: Session, kind: str, title: str, body: str) -> None:
        """Tell the student: their own process, the inbox (event log) and any configured webhooks."""
        if not self.notifier.wants(kind):
            return
        s.send({"type": "notify", "kind": kind, "title": title, "body": body})
        if self.inbox:
            self._event("notify", s, what=kind, title=title, body=body)
        self.notifier.push(s.user, kind, title, body)

    # ------------------------------------------------------------ client lifecycle
    def register(self, handle: Any, msg: dict[str, Any]) -> dict[str, Any]:
        now = self.clock()
        sid = f"s{self._next_id}"
        self._next_id += 1
        s = Session(sid, str(msg.get("user", "?")), str(msg.get("name") or msg.get("user", "?")),
                    int(msg.get("pid", 0)), handle, bool(msg.get("whitelist", False)),
                    registered_at=now, last_report=now, last_active=now)
        if s.user in self.policy.whitelist_users:
            s.whitelist = True
        self.sessions[sid] = s
        self._event("register", s, pid=s.pid, whitelist=s.whitelist)
        slot = self.slots.free_slot()
        if slot is not None and len(self.queue) == 0:
            self._assign(s, slot)
            s.state = ACTIVE
            self._event("grant", s, slot=slot, waited_s=0.0)
            return {"type": "registered", "session": sid, "state": "running", "slot": slot,
                    "cap_fraction": self.slots.cap_fraction}
        s.state, s.queued_since = QUEUED, now
        self.queue.push(QueueEntry(sid, s.user, now))
        self._event("queued", s, position=len(self.queue))
        return {"type": "registered", "session": sid, "state": "queued", "slot": None,
                "cap_fraction": self.slots.cap_fraction}

    def on_message(self, sid: str, msg: dict[str, Any]) -> None:
        s = self.sessions.get(sid)
        if s is None:
            return
        now = self.clock()
        kind = msg.get("type")
        s.last_report = now
        if kind == "report":
            s.mem_reserved = int(msg.get("mem_reserved", 0))
            s.mem_allocated = int(msg.get("mem_allocated", 0))
            s.peak_allocated = max(s.peak_allocated, int(msg.get("peak_allocated", s.mem_allocated)))
            s.steps = int(msg.get("steps", s.steps))
        elif kind == "request":
            self._owner_returns(s)
        elif kind == "parked":
            self._parked(s, float(msg.get("park_s", 0.0)), int(msg.get("bytes", 0)))
        elif kind == "park_failed":
            s.state, s.last_active = ACTIVE, now
            self._event("park_failed", s, error=msg.get("error", ""))
        elif kind == "resumed":
            self._resumed(s, float(msg.get("restore_s", 0.0)))
        elif kind == "resume_failed":
            self._event("resume_failed", s, error=msg.get("error", ""))
        elif kind == "spilled":
            self._spilled(s, float(msg.get("seconds", 0.0)), int(msg.get("bytes", 0)))
        elif kind == "spill_failed":
            self._event("spill_failed", s, error=msg.get("error", ""))
        elif kind in ("snooze", "away"):   # asked by the student's own process
            reply = self.snooze(s) if kind == "snooze" else self.away(s, msg.get("minutes"), msg.get("until"))
            s.send(reply if reply["type"] == "error" else {**reply, "type": f"{kind}_ok"})
        elif kind == "bye":
            self.disconnect(sid, reason="bye")

    def disconnect(self, sid: str, reason: str = "disconnect") -> None:
        s = self.sessions.pop(sid, None)
        if s is None:
            return
        now = self.clock()
        self.queue.remove(sid)
        if s.state == PARKED:
            s.parked_seconds += now - s.parked_at
            self.reclaimed_closed_s += now - s.parked_at
            self._close_receipt(s, now)
        if s.dry_parked_at:
            self._event("would_restore", s, would_reclaim_s=now - s.dry_parked_at, reason="session ended")
        self._release(s)
        self._event("gone", s, reason=reason, parks=s.parks)
        if self.store:
            self.store.save_session({"sid": s.sid, "user": s.user, "name": s.name, "pid": s.pid,
                                     "registered_at": s.registered_at, "ended_at": now, "parks": s.parks,
                                     "restores": s.restores, "gpu_seconds": s.gpu_seconds,
                                     "parked_seconds": s.parked_seconds, "peak_allocated": s.peak_allocated,
                                     "snoozes": s.snoozes})
        self._dispatch()

    # ------------------------------------------------------------ slots + queue
    def _assign(self, s: Session, slot: int) -> None:
        self.slots.assign(slot, s.sid)
        s.slot, s.slot_since = slot, self.clock()

    def _release(self, s: Session, freed_by_park: bool = False) -> None:
        slot = self.slots.release(s.sid)
        if slot is None:
            return
        now = self.clock()
        held = now - s.slot_since
        s.gpu_seconds += held
        self.usage.add(s.user, held, now)
        s.slot = None
        if freed_by_park:
            self._freed_by_park[slot] = s.sid
        else:
            self._freed_by_park.pop(slot, None)

    def _dispatch(self) -> None:
        now = self.clock()
        while self.slots.n_free() and len(self.queue):
            entry = self.queue.pop(now)
            s = self.sessions.get(entry.sid)
            if s is None:
                continue
            slot = self.slots.free_slot()
            owner_sid = self._freed_by_park.pop(slot, None)
            self._assign(s, slot)
            waited = now - entry.since
            if s.state == PARKED:   # a returning owner: restore onto the slot
                s.state = RESTORING
                self._event("restoring", s, slot=slot, waited_s=waited)
                res = self.parker.resume(s, slot, self.slots.cap_fraction)
                if res is not None:
                    self._resumed(s, res.seconds) if res.ok else self._event("resume_failed", s, error=res.error)
            else:                   # a new session leaving the queue
                s.state, s.last_active, s.notified_next = ACTIVE, now, False
                s.send({"type": "grant", "slot": slot, "cap_fraction": self.slots.cap_fraction})
                self._event("grant", s, slot=slot, waited_s=waited)
                self._notify(s, "ready", "Your GPU is ready",
                             f"You got slot {slot} after waiting {_fmt_wait(waited)}.")
            owner = self.sessions.get(owner_sid) if owner_sid else None
            if owner is not None and owner.state == PARKED and owner.sid != s.sid:
                r = Receipt(s.user, owner.user, owner.sid, now, waited)
                self.receipts.append(r)
                self._open_receipt[owner.sid] = r
                self._event("receipt", s, from_user=owner.user, slot=slot, waited_s=waited)
                self._event("gave_slot", owner, to_user=s.user, slot=slot, waited_s=waited)

    def _close_receipt(self, owner: Session, now: float) -> None:
        r = self._open_receipt.pop(owner.sid, None)
        if r is not None:
            r.closed_at = now
            self._event("receipt_closed", owner, beneficiary=r.beneficiary, saved_s=r.saved_s(now))

    # ------------------------------------------------------------ park / restore
    def _park(self, s: Session, reason: str) -> None:
        if not self.parker.can_park(s):
            self._event("cannot_park", s, reason="process does not use gpuq_client")
            s.state, s.last_active = IDLE, self.clock()
            return
        s.state, s.park_started = PARKING, self.clock()
        self._event("park", s, reason=reason, mem_mib=round(s.mem_reserved / 2**20))
        res = self.parker.park(s)
        if res is not None:
            if res.ok:
                self._parked(s, res.seconds, res.bytes)
            else:
                s.state, s.last_active = ACTIVE, self.clock()
                self._event("park_failed", s, error=res.error)

    def _parked(self, s: Session, park_s: float, nbytes: int) -> None:
        if s.state != PARKING:
            return
        now = self.clock()
        s.state, s.parked_at, s.parks, s.last_park_s = PARKED, now, s.parks + 1, park_s
        self.total_parks += 1
        self._release(s, freed_by_park=True)
        self._event("parked", s, park_s=park_s, gib=round(nbytes / 2**30, 3))
        back = ", or it comes back on its own just before you return." if s.back_at else "."
        self._notify(s, "parked", "Session parked, your work is safe",
                     f"{nbytes / 2**30:.2f} GiB moved to RAM in {park_s:.2f} s and the GPU went to the next "
                     f"person. Run anything to get it back{back}")
        self._dispatch()
        if s.pending_request:
            s.pending_request = False
            self._owner_returns(s)

    def _owner_returns(self, s: Session) -> None:
        now = self.clock()
        if s.state == PARKING:
            s.pending_request = True
            return
        if s.state != PARKED or s.sid in self.queue:
            return
        s.request_since = now
        self._close_receipt(s, now)
        self.queue.push(QueueEntry(s.sid, s.user, now, returning=True))
        self._event("owner_returned", s, free_slots=self.slots.n_free())
        self._dispatch()

    def _resumed(self, s: Session, restore_s: float) -> None:
        if s.state != RESTORING:
            return
        now = self.clock()
        s.parked_seconds += now - s.parked_at
        self.reclaimed_closed_s += now - s.parked_at
        s.state, s.last_active, s.restores, s.last_restore_s = ACTIVE, now, s.restores + 1, restore_s
        s.steps_at_tick = s.steps
        s.on_disk = s.spill_requested = False
        self.total_restores += 1
        self._event("restored", s, restore_s=restore_s, waited_s=now - s.request_since, slot=s.slot)
        self._notify(s, "restored", "Welcome back",
                     f"Restored onto slot {s.slot} in {restore_s:.2f} s, every tensor exactly as you left it.")

    def _spilled(self, s: Session, seconds: float, nbytes: int) -> None:
        if s.state != PARKED:
            return
        s.on_disk = True
        self._event("spilled", s, seconds=seconds, gib=round(nbytes / 2**30, 3))
        self._notify(s, "spilled", "Session moved to disk",
                     f"Parked for {_fmt_wait(self.clock() - s.parked_at)}, so its {nbytes / 2**30:.2f} GiB moved "
                     "from RAM to disk. Restoring takes a few seconds longer.")

    # ------------------------------------------------------------ snooze, away, deadline mode
    def _idle_for(self, s: Session, now: float) -> float:
        """Seconds without compute, counting a snooze as activity until (snooze end - idle window).

        So after a snooze the warning comes exactly when the snooze ends, if the session is still idle.
        """
        return now - max(s.last_active, s.snooze_until - self.policy.idle_window_s)

    def snoozes_left(self, user: str, now: float | None = None) -> int:
        today = time.strftime("%Y-%m-%d", time.localtime(self.clock() if now is None else now))
        day, used = self._snooze_count.get(user, (today, 0))
        return self.snoozes_per_day - (used if day == today else 0)

    def snooze(self, s: Session) -> dict[str, Any]:
        now = self.clock()
        if s.state not in (ACTIVE, IDLE, WARNED):
            return {"type": "error", "error": f"cannot snooze a {s.state} session"}
        left = self.snoozes_left(s.user, now)
        if left <= 0:
            return {"type": "error", "error": f"no snoozes left today ({self.snoozes_per_day} per day)"}
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        self._snooze_count[s.user] = (today, self.snoozes_per_day - left + 1)
        s.snooze_until, s.snoozes = now + self.snooze_s, s.snoozes + 1
        if s.state == WARNED:
            s.state = IDLE   # warning withdrawn; the idle clock now runs from the snooze
        self._event("snoozed", s, for_s=self.snooze_s, left=left - 1)
        return {"type": "ok", "session": s.sid, "state": s.state, "snooze_s": self.snooze_s,
                "snoozes_left": left - 1}

    def away(self, s: Session, minutes: float | None = None, until: float | None = None) -> dict[str, Any]:
        """'Back at 3pm': park now and restore shortly before the announced return."""
        now = self.clock()
        if until is None:
            if minutes is None:
                return {"type": "error", "error": "say how long you'll be away (minutes or until)"}
            until = now + float(minutes) * 60
        until = float(until)
        if until <= now:
            return {"type": "error", "error": "the return time is in the past"}
        if s.state not in (ACTIVE, IDLE, WARNED, PARKING, PARKED):
            return {"type": "error", "error": f"cannot park a {s.state} session"}
        if s.state in (ACTIVE, IDLE, WARNED) and not self.parker.can_park(s):
            return {"type": "error", "error": "this process can't be parked (it doesn't use the helper library)"}
        s.back_at = until
        self._event("away", s, until=until, until_text=time.strftime("%H:%M", time.localtime(until)))
        if s.state in (ACTIVE, IDLE, WARNED):
            self._park(s, reason="away")
        return {"type": "ok", "session": s.sid, "state": s.state, "back_at": until}

    def set_mode(self, msg: dict[str, Any]) -> dict[str, Any]:
        minutes = msg.get("minutes")
        self.runtime_mode = {"name": msg.get("name") or "deadline mode",
                             "idle_window_s": msg.get("idle_window_s"), "grace_period_s": msg.get("grace_period_s"),
                             "until": self.clock() + float(minutes) * 60 if minutes else None}
        self._refresh_policy(self.clock())
        return {"type": "ok", "mode": self.mode}

    def _refresh_policy(self, now: float) -> None:
        fields, mode = effective_overrides(self.schedules, self.runtime_mode, now)
        self.policy = dataclasses.replace(self.base_policy, **fields)
        if (mode or {}).get("name") != (self.mode or {}).get("name"):
            self._event("mode", None, name=mode["name"] if mode else "normal",
                        source=mode["source"] if mode else None, **fields)
        self.mode = mode

    # ------------------------------------------------------------ the periodic tick
    def tick(self) -> None:
        now = self.clock()
        self._refresh_policy(now)
        dt = 0.0 if self._last_tick is None else max(0.0, now - self._last_tick)
        self._last_tick = now
        sample = self.monitor.sample()
        self.last_sample = sample
        if self.dry_run:
            n_dry = sum(1 for s in self.sessions.values() if s.dry_parked_at and s.slot is not None)
            self.dry_would_reclaim_s += n_dry * dt
            self.dry_saved_wait_s += min(n_dry, len(self.queue)) * dt
        for s in list(self.sessions.values()):
            if now - s.last_report > self.report_timeout_s:
                self.disconnect(s.sid, reason="silent")
                continue
            if s.state == PARKING and now - s.park_started > self.park_timeout_s:
                s.state, s.last_active = ACTIVE, now
                self._event("park_timeout", s)
                continue
            if s.state == PARKED:
                self._tick_parked(s, now)
                continue
            if s.state not in (ACTIVE, IDLE, WARNED):
                continue
            s.util = int(sample.util_by_pid.get(s.pid, 0))
            advanced = s.steps > s.steps_at_tick
            s.steps_at_tick = s.steps
            computing = is_computing(s.util, advanced, self.policy)
            if computing:
                s.last_active = now
            if s.dry_parked_at:   # dry run: counts as parked until it computes again
                if computing:
                    self._event("would_restore", s, would_reclaim_s=now - s.dry_parked_at)
                    s.dry_parked_at, s.state = 0.0, ACTIVE
                continue
            idle_for = self._idle_for(s, now)
            action = decide(s.state, idle_for, now - s.warned_at, s.mem_reserved / 2**20, s.util,
                            s.whitelist, self.policy)
            self._apply(s, action, idle_for, now)
        self._tick_queue(now)

    def _tick_parked(self, s: Session, now: float) -> None:
        if s.back_at is not None and now >= s.back_at - self.away_prewarm_s:
            s.back_at = None
            self._event("prewarm", s)
            self._owner_returns(s)
            return
        if (self.spill_after_s > 0 and not s.on_disk and not s.spill_requested and s.sid not in self.queue
                and now - s.parked_at >= self.spill_after_s):
            s.spill_requested = True
            res = self.parker.spill(s, self.spill_dir)
            if res is not None:
                if res.ok:
                    self._spilled(s, res.seconds, res.bytes)
                else:
                    self._event("spill_failed", s, error=res.error)

    def _tick_queue(self, now: float) -> None:
        """'You're next in line': sent once to whoever reaches the front of the queue."""
        ordered = self.queue.ordered(now)
        if not ordered:
            return
        s = self.sessions.get(ordered[0].sid)
        if s is None or s.notified_next or s.state != QUEUED:
            return
        s.notified_next = True
        eta, basis = self.queue_etas(now)[0]
        about = "about " if basis == "guess" else ""
        self._notify(s, "next", "You're next in line",
                     f"Estimated wait: {about}{_fmt_wait(eta)}. We'll tell you when your GPU is ready.")

    def _apply(self, s: Session, action: str | None, idle_for: float, now: float) -> None:
        if action == "activate":
            if s.state == WARNED:
                self._event("warn_cancelled", s, note="GPU work resumed before the park")
            elif s.state == IDLE:
                self._event("active", s)
            s.state = ACTIVE
        elif action == "idle":
            s.state = IDLE
            self._event("idle", s, mem_mib=round(s.mem_reserved / 2**20))
        elif action == "warn":
            if self.dry_run:
                self._event("would_warn", s, idle_s=idle_for)
                s.state, s.warned_at = WARNED, now
                return
            s.state, s.warned_at = WARNED, now
            left = self.snoozes_left(s.user, now)
            s.send({"type": "warn", "park_in_s": self.policy.grace_period_s, "idle_s": idle_for,
                    "snoozes_left": left})
            self._event("warn", s, idle_s=idle_for, park_in_s=self.policy.grace_period_s)
            self._notify(s, "warn", f"Parking your GPU session in {_fmt_wait(self.policy.grace_period_s)}",
                         f"No GPU work for {_fmt_wait(idle_for)}. Run something, or snooze ({left} left "
                         "today). Your work is kept either way.")
        elif action == "park":
            if self.dry_run:
                self._event("would_park", s, idle_s=idle_for)
                s.state, s.dry_parked_at = IDLE, now   # counts as parked until the session computes again
                return
            self._park(s, reason=f"idle {idle_for:.0f} s")

    # ------------------------------------------------------------ control + status
    def _find(self, key: str | None) -> Session | None:
        if key is None:
            return None
        if key in self.sessions:
            return self.sessions[key]
        matches = [s for s in self.sessions.values() if s.user == key or s.name == key]
        return matches[0] if matches else None

    def control(self, msg: dict[str, Any]) -> dict[str, Any]:
        kind = msg.get("type")
        if kind == "status":
            return {"type": "status", "status": self.status()}
        s = self._find(msg.get("session"))
        if kind == "park_now":
            if s is None or s.state not in (ACTIVE, IDLE, WARNED):
                return {"type": "error", "error": "no parkable session with that id/user"}
            self._event("park_now", s, note="manual (demo control)")
            self._park(s, reason="manual")
            return {"type": "ok", "session": s.sid, "state": s.state}
        if kind == "resume":
            if s is None or s.state != PARKED:
                return {"type": "error", "error": "no parked session with that id/user"}
            s.back_at = None
            self._owner_returns(s)
            return {"type": "ok", "session": s.sid, "state": s.state}
        if kind == "snooze":
            return self.snooze(s) if s else {"type": "error", "error": "no session with that id/user"}
        if kind == "away":
            if s is None:
                return {"type": "error", "error": "no session with that id/user"}
            return self.away(s, msg.get("minutes"), msg.get("until"))
        if kind == "set_mode":
            return self.set_mode(msg)
        if kind == "clear_mode":
            self.runtime_mode = None
            self._refresh_policy(self.clock())
            return {"type": "ok", "mode": self.mode}
        return {"type": "error", "error": f"unknown control message {kind!r}"}

    def reclaimed_seconds(self) -> float:
        now = self.clock()
        ongoing = sum(now - s.parked_at for s in self.sessions.values() if s.state == PARKED)
        return self.reclaimed_closed_s + ongoing

    def queue_etas(self, now: float) -> list[tuple[float | None, str]]:
        views = []
        for h in self.slots.holder:
            s = self.sessions.get(h) if h else None
            if s is None:
                views.append(SlotView(None))
                continue
            views.append(SlotView(s.state, self._idle_for(s, now) if s.state in (ACTIVE, IDLE, WARNED) else 0.0,
                                  max(0.0, self.policy.grace_period_s - (now - s.warned_at)), s.whitelist))
        return queue_etas(views, len(self.queue), self.policy.idle_window_s, self.policy.grace_period_s,
                          self.eta_active_guess_s)

    def status(self) -> dict[str, Any]:
        now = self.clock()
        smp = self.last_sample
        cap_bytes = int(self.slots.cap_gib * 2**30)
        sessions = []
        for s in self.sessions.values():
            holding = now - s.slot_since if s.slot is not None else 0.0
            parked_now = now - s.parked_at if s.state == PARKED else 0.0
            sessions.append({
                "sid": s.sid, "user": s.user, "name": s.name, "pid": s.pid, "state": s.state, "slot": s.slot,
                "mem_mib": round(s.mem_reserved / 2**20), "util": s.util,
                "idle_s": round(max(0.0, now - s.last_active), 1) if s.state in (ACTIVE, IDLE, WARNED) else None,
                "park_in_s": round(max(0.0, self.policy.grace_period_s - (now - s.warned_at)), 1)
                if s.state == WARNED else None,
                "parked_s": round(parked_now, 1) if s.state == PARKED else None,
                "parks": s.parks, "restores": s.restores, "last_park_s": s.last_park_s,
                "last_restore_s": s.last_restore_s, "whitelist": s.whitelist,
                "snoozed_for_s": round(s.snooze_until - now, 1) if s.snooze_until > now else None,
                "snoozes_left": self.snoozes_left(s.user, now),
                "back_at": s.back_at, "on_disk": s.on_disk, "dry_parked": bool(s.dry_parked_at),
                "peak_mib": round(s.peak_allocated / 2**20),
                "tip": memory_tip(s.peak_allocated, cap_bytes, s.steps),
                "gpu_s": round(s.gpu_seconds + holding, 1),
                "parked_total_s": round(s.parked_seconds + parked_now, 1)})
        by_sid = {x["sid"]: x for x in sessions}
        etas = self.queue_etas(now)
        return {
            "time": now, "started_at": self.started_at, "dry_run": self.dry_run, "parker": self.parker.name,
            "gpu": {"used_mib": round(smp.used_mib), "total_mib": round(smp.total_mib), "util": smp.util_pct,
                    "temp_c": smp.temp_c} if smp else None,
            "slots": [{"slot": i, "cap_gib": self.slots.cap_gib, "holder": by_sid.get(h) if h else None}
                      for i, h in enumerate(self.slots.holder)],
            "queue": [{"position": i + 1, "sid": e.sid, "user": e.user, "waiting_s": round(now - e.since, 1),
                       "returning": e.returning,
                       "eta_s": None if etas[i][0] is None else round(etas[i][0], 1), "eta_basis": etas[i][1]}
                      for i, e in enumerate(self.queue.ordered(now))],
            "sessions": sessions,
            "events": list(self.events)[-60:],
            "stats": {
                "reclaimed_gpu_s": round(self.reclaimed_seconds(), 1),
                "parks": self.total_parks,
                "restores": self.total_restores,
                "dry_would_reclaim_s": round(self.dry_would_reclaim_s, 1),
                "dry_saved_wait_s": round(self.dry_saved_wait_s, 1),
                "receipts": [{"beneficiary": r.beneficiary, "owner": r.owner, "waited_s": round(r.waited_s, 1),
                              "saved_s": round(r.saved_s(now), 1), "open": r.closed_at is None}
                             for r in self.receipts[-20:]]},
            "policy": {"idle_window_s": self.policy.idle_window_s, "grace_period_s": self.policy.grace_period_s,
                       "util_threshold_pct": self.policy.util_threshold_pct, "snooze_s": self.snooze_s,
                       "snoozes_per_day": self.snoozes_per_day, "spill_after_s": self.spill_after_s},
            "mode": self.mode,
        }
