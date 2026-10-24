"""Lab dashboard: FastAPI backend + one static page that polls it.

    python -m baton.dashboard.app          # http://127.0.0.1:8000

The dashboard never needs the daemon to draw a page: it reads the daemon's SQLite snapshot (written
every tick) and the event log. Only the control endpoints (park now, resume, snooze, away, mode) talk
to the daemon, over the same JSON-lines TCP channel the CLI uses.

Every endpoint is listed in API.md; FastAPI also serves interactive docs at /docs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import protocol
from ..config import load_config, resolve_path
from ..daemon.dryrun import dryrun_report
from ..daemon.explain import timeline
from ..daemon.state import read_events, read_session_events, read_sessions, read_snapshot
from ..daemon.stats import leaderboard, user_stats


class AwayBody(BaseModel):
    minutes: float | None = None
    until: float | None = None       # epoch seconds


class ModeBody(BaseModel):
    name: str = "deadline mode"
    idle_window_s: float | None = None
    grace_period_s: float | None = None
    minutes: float | None = None     # how long the mode lasts; None = until cleared

STATIC = Path(__file__).parent / "static"


def create_app(cfg: dict | None = None) -> FastAPI:
    cfg = cfg or load_config()
    db = resolve_path(cfg["daemon"]["db_path"])
    results = resolve_path("results")
    host, port = cfg["daemon"]["host"], cfg["daemon"]["port"]
    app = FastAPI(title="Baton")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/results")
    def results_page():
        return FileResponse(STATIC / "results.html")

    @app.get("/api/status")
    def status():
        snap = read_snapshot(db)
        if snap is None:
            return JSONResponse({"online": False, "reason": "no daemon snapshot yet"})
        age = time.time() - snap["time"]
        return JSONResponse({"online": age < 5, "age_s": round(age, 1), **snap})

    @app.get("/api/events")
    def events(limit: int = 100, after_id: int = 0):
        return read_events(db, limit=min(limit, 500), after_id=after_id)

    @app.get("/api/stream")
    async def stream(request: Request, once: bool = False):
        """Server-sent events: `status` whenever the snapshot changes, `events` for new log entries."""
        async def gen():
            last_ts, last_id = None, 0
            latest = read_events(db, limit=1)
            if latest:
                last_id = latest[0]["id"]
            while True:
                snap = read_snapshot(db)
                if snap is not None and snap["time"] != last_ts:
                    last_ts = snap["time"]
                    yield f"event: status\ndata: {json.dumps(snap)}\n\n"
                new = read_events(db, limit=200, after_id=last_id)
                if new:
                    last_id = new[0]["id"]
                    yield f"event: events\ndata: {json.dumps(list(reversed(new)))}\n\n"
                if once or await request.is_disconnected():
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/sessions/{sid}/timeline")
    def session_timeline(sid: str):
        """'Why was I parked?': the latest session with this id, every event explained."""
        evs = read_session_events(db, sid)
        if not evs:
            raise HTTPException(404, f"no events for session {sid}")
        return {"sid": sid, "user": evs[0]["user"], "timeline": timeline(evs)}

    @app.get("/api/users/{user}")
    def user_view(user: str, days: float = 7):
        """Everything one student needs: live sessions, stats, notifications, receipts, past sessions."""
        now = time.time()
        snap = read_snapshot(db) or {}
        live = [s for s in snap.get("sessions", []) if s["user"] == user]
        receipts = [r for r in snap.get("stats", {}).get("receipts", []) if user in (r["owner"], r["beneficiary"])]
        stats = user_stats(db, now - days * 86400, now).get(user)
        return {"user": user, "days": days, "live_sessions": live, "stats": stats, "receipts": receipts,
                "notifications": read_events(db, limit=30, kinds=["notify"], user=user),
                "past_sessions": read_sessions(db, now - days * 86400, user)[-20:]}

    @app.get("/api/notifications")
    def notifications(user: str | None = None, after_id: int = 0, limit: int = 50):
        return read_events(db, limit=min(limit, 500), after_id=after_id, kinds=["notify"], user=user)

    @app.get("/api/leaderboard")
    def lab_leaderboard(days: float = 7):
        return leaderboard(db, days)

    @app.get("/api/dryrun_report")
    def dry_report(days: float | None = None):
        return dryrun_report(db, time.time() - days * 86400 if days else 0.0)

    @app.get("/api/results")
    def sim_results():
        path = results / "sim_results.json"
        if not path.exists():
            raise HTTPException(404, "run python -m baton.simulator.report first")
        data = json.loads(path.read_text(encoding="utf-8"))
        scen = results / "scenario_run1.json"
        data["scenario"] = json.loads(scen.read_text(encoding="utf-8")) if scen.exists() else None
        bench = results / "park_bench.md"
        data["park_bench_md"] = bench.read_text(encoding="utf-8") if bench.exists() else ""
        return data

    @app.post("/api/park_now/{session}")
    def park_now(session: str):
        return _control({"type": "park_now", "session": session})

    @app.post("/api/resume/{session}")
    def resume(session: str):
        return _control({"type": "resume", "session": session})

    @app.post("/api/snooze/{session}")
    def snooze(session: str):
        return _control({"type": "snooze", "session": session})

    @app.post("/api/away/{session}")
    def away(session: str, body: AwayBody):
        return _control({"type": "away", "session": session, "minutes": body.minutes, "until": body.until})

    @app.post("/api/mode")
    def set_mode(body: ModeBody):
        return _control({"type": "set_mode", **body.model_dump()})

    @app.delete("/api/mode")
    def clear_mode():
        return _control({"type": "clear_mode"})

    def _control(msg: dict):
        try:
            reply = protocol.request(host, port, msg, timeout=3)
        except OSError as e:
            raise HTTPException(503, f"daemon not reachable: {e}")
        if reply is None or reply.get("type") == "error":
            raise HTTPException(400, (reply or {}).get("error", "no reply"))
        return reply

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.mount("/files", StaticFiles(directory=results), name="files")
    return app


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    cfg = load_config()
    ap = argparse.ArgumentParser(description="Baton dashboard")
    ap.add_argument("--host", default=cfg["dashboard"]["host"])
    ap.add_argument("--port", type=int, default=cfg["dashboard"]["port"])
    ap.add_argument("--db", default=None, help="read this SQLite file instead (e.g. data/preview.db)")
    args = ap.parse_args(argv)
    if args.db:
        cfg["daemon"]["db_path"] = args.db
    print(json.dumps({"event": "pid", "pid": __import__("os").getpid()}), flush=True)
    uvicorn.run(create_app(cfg), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
