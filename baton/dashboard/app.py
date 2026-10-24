"""Lab dashboard: FastAPI backend + one static page that polls it.

    python -m baton.dashboard.app          # http://127.0.0.1:8000

The dashboard never needs the daemon to draw a page: it reads the daemon's SQLite snapshot (written
every tick) and the event log. Only the two demo-control buttons (park now / resume) talk to the
daemon, over the same JSON-lines TCP channel the CLI uses.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import protocol
from ..config import load_config, resolve_path
from ..daemon.state import read_events, read_snapshot

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
    def events(limit: int = 100):
        return read_events(db, limit=min(limit, 500))

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
    args = ap.parse_args(argv)
    print(json.dumps({"event": "pid", "pid": __import__("os").getpid()}), flush=True)
    uvicorn.run(create_app(cfg), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
