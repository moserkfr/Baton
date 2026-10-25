"""SQLite persistence: an append-only event log and the latest status snapshot.

The daemon is the only writer. The dashboard and CLI only read, so they never need to talk to the
daemon to draw a page, and a crashed daemon still leaves its history behind.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    sid TEXT,
    user TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS snapshot (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    ts REAL NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sid TEXT,
    user TEXT, name TEXT, pid INTEGER,
    registered_at REAL, ended_at REAL,
    parks INTEGER, restores INTEGER, gpu_seconds REAL, parked_seconds REAL,
    peak_allocated INTEGER DEFAULT 0, snoozes INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_sid ON events (sid);
CREATE INDEX IF NOT EXISTS events_kind ON events (kind);
"""
SESSION_COLS = ("sid", "user", "name", "pid", "registered_at", "ended_at", "parks", "restores", "gpu_seconds",
                "parked_seconds", "peak_allocated", "snoozes")


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(sessions)")]
        if cols and "id" not in cols:
            # first schema keyed sessions by sid, which repeats across daemon runs (s1, s2, ...):
            # keep the old rows by copying them into the new table
            self.db.execute("ALTER TABLE sessions RENAME TO sessions_v1")
            self.db.executescript(SCHEMA)
            self.db.execute("INSERT INTO sessions (sid, user, name, pid, registered_at, ended_at, parks, restores, "
                            "gpu_seconds, parked_seconds) SELECT * FROM sessions_v1")
            self.db.execute("DROP TABLE sessions_v1")
        self.db.executescript(SCHEMA)

    def event(self, ts: float, kind: str, sid: str | None, user: str | None, detail: dict[str, Any]) -> None:
        with self._lock:
            self.db.execute("INSERT INTO events (ts, kind, sid, user, detail) VALUES (?,?,?,?,?)",
                            (ts, kind, sid, user, json.dumps(detail)))

    def save_snapshot(self, ts: float, status: dict[str, Any]) -> None:
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO snapshot (id, ts, status) VALUES (1, ?, ?)",
                            (ts, json.dumps(status)))

    def save_session(self, row: dict[str, Any]) -> None:
        with self._lock:
            row = {"peak_allocated": 0, "snoozes": 0, **row}
            self.db.execute(f"INSERT INTO sessions ({','.join(SESSION_COLS)}) "
                            f"VALUES ({','.join(':' + c for c in SESSION_COLS)})", row)

    def close(self) -> None:
        with self._lock:
            self.db.close()


def clear(path: str | Path) -> None:
    """Empty every table for a fresh run. Unlike deleting the file, this works while readers (an open
    dashboard tab streaming status) have it open, which Windows would refuse."""
    if not Path(path).exists():
        return
    db = sqlite3.connect(path, timeout=10)
    try:
        for table in ("events", "snapshot", "sessions"):
            try:
                db.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                pass   # table not created yet
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------- readers (dashboard, CLI)

def _ro(path: str | Path) -> sqlite3.Connection | None:
    if not Path(path).exists():
        return None
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def _event_rows(rows) -> list[dict[str, Any]]:
    return [{"id": r[0], "ts": r[1], "kind": r[2], "sid": r[3], "user": r[4], "detail": json.loads(r[5] or "{}")}
            for r in rows]


def read_snapshot(path: str | Path) -> dict[str, Any] | None:
    db = _ro(path)
    if db is None:
        return None
    try:
        row = db.execute("SELECT ts, status FROM snapshot WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        db.close()
    return json.loads(row[1]) if row else None


def read_snapshot_time(path: str | Path) -> float | None:
    """Just the snapshot's timestamp: a cheap 'has anything changed?' check for polling readers."""
    db = _ro(path)
    if db is None:
        return None
    try:
        row = db.execute("SELECT ts FROM snapshot WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        db.close()
    return row[0] if row else None


def read_events(path: str | Path, since_ts: float = 0.0, limit: int = 200, after_id: int = 0,
                kinds: list[str] | None = None, user: str | None = None) -> list[dict[str, Any]]:
    """Newest first. `after_id` lets a stream fetch only what it hasn't seen."""
    db = _ro(path)
    if db is None:
        return []
    where, args = ["ts >= ?", "id > ?"], [since_ts, after_id]
    if kinds:
        where.append(f"kind IN ({','.join('?' * len(kinds))})")
        args += kinds
    if user:
        where.append("user = ?")
        args.append(user)
    try:
        rows = db.execute(f"SELECT id, ts, kind, sid, user, detail FROM events WHERE {' AND '.join(where)} "
                          "ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        db.close()
    return _event_rows(rows)


def read_session_events(path: str | Path, sid: str) -> list[dict[str, Any]]:
    """Every event of the latest session with this id, oldest first.

    Session ids restart at s1 with every daemon run, so we start from that id's most recent `register`.
    """
    db = _ro(path)
    if db is None:
        return []
    try:
        start = db.execute("SELECT MAX(id) FROM events WHERE sid = ? AND kind = 'register'", (sid,)).fetchone()[0]
        if start is None:
            return []
        end = db.execute("SELECT MIN(id) FROM events WHERE id > ? AND sid = ? AND kind = 'register'",
                         (start, sid)).fetchone()[0]
        rows = db.execute("SELECT id, ts, kind, sid, user, detail FROM events WHERE sid = ? AND id >= ? AND id < ? "
                          "ORDER BY id", (sid, start, end or 2**62)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        db.close()
    return _event_rows(rows)


def read_sessions(path: str | Path, since_ts: float = 0.0, user: str | None = None) -> list[dict[str, Any]]:
    """Finished sessions (the daemon writes a row when a session ends)."""
    db = _ro(path)
    if db is None:
        return []
    sql = f"SELECT {','.join(SESSION_COLS)} FROM sessions WHERE ended_at >= ?"
    args: list[Any] = [since_ts]
    if user:
        sql += " AND user = ?"
        args.append(user)
    try:
        rows = db.execute(sql + " ORDER BY id", args).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        db.close()
    return [dict(zip(SESSION_COLS, r)) for r in rows]
