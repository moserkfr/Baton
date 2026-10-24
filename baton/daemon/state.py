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
    sid TEXT PRIMARY KEY,
    user TEXT, name TEXT, pid INTEGER,
    registered_at REAL, ended_at REAL,
    parks INTEGER, restores INTEGER, gpu_seconds REAL, parked_seconds REAL
);
"""


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
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
            self.db.execute(
                "INSERT OR REPLACE INTO sessions VALUES (:sid,:user,:name,:pid,:registered_at,:ended_at,"
                ":parks,:restores,:gpu_seconds,:parked_seconds)", row)

    def close(self) -> None:
        with self._lock:
            self.db.close()


# ---------------------------------------------------------------- readers (dashboard, CLI)

def read_snapshot(path: str | Path) -> dict[str, Any] | None:
    if not Path(path).exists():
        return None
    db = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        row = db.execute("SELECT ts, status FROM snapshot WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        db.close()
    return json.loads(row[1]) if row else None


def read_events(path: str | Path, since_ts: float = 0.0, limit: int = 200) -> list[dict[str, Any]]:
    if not Path(path).exists():
        return []
    db = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT ts, kind, sid, user, detail FROM events WHERE ts >= ? "
                          "ORDER BY id DESC LIMIT ?", (since_ts, limit)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        db.close()
    return [{"ts": r[0], "kind": r[1], "sid": r[2], "user": r[3], "detail": json.loads(r[4] or "{}")}
            for r in rows]
