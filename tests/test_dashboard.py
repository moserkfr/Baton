"""Dashboard API against a snapshot written by a fake-time daemon (no GPU, no daemon process)."""
import time

from fastapi.testclient import TestClient

from baton.config import load_config
from baton.daemon.core import Daemon
from baton.daemon.monitor import FakeMonitor
from baton.daemon.state import StateStore
from baton.dashboard.app import create_app
from baton.parkers.cooperative import CooperativeParker


class Handle:
    def send(self, msg):
        pass


def test_status_events_and_pages(tmp_path):
    db = tmp_path / "state.db"
    cfg = load_config(overrides={"daemon.db_path": str(db), "daemon.dry_run": False, "slots.count": 1})
    store = StateStore(db)
    d = Daemon(cfg, FakeMonitor(), CooperativeParker(), store, clock=time.time)
    b = d.register(Handle(), {"user": "student-B", "pid": 1, "name": "B"})["session"]
    d.on_message(b, {"type": "report", "mem_reserved": 900 * 2**20, "steps": 1})
    c = d.register(Handle(), {"user": "student-C", "pid": 2, "name": "C"})["session"]
    d.control({"type": "park_now", "session": b})
    d.on_message(b, {"type": "parked", "park_s": 0.3, "bytes": 2**30})
    d.tick()
    store.save_snapshot(time.time(), d.status())

    client = TestClient(create_app(cfg))
    st = client.get("/api/status").json()
    assert st["online"] is True
    assert st["slots"][0]["holder"]["name"] == "C"
    assert [s["state"] for s in st["sessions"] if s["name"] == "B"] == ["parked"]
    assert st["stats"]["receipts"][0]["beneficiary"] == "student-C"
    kinds = [e["kind"] for e in client.get("/api/events").json()]
    assert "parked" in kinds and "receipt" in kinds
    assert "Slot map" in client.get("/").text
    assert client.get("/results").status_code == 200
    assert client.get("/static/style.css").status_code == 200
    # control endpoints report a missing daemon cleanly
    assert client.post("/api/park_now/s1").status_code in (400, 503)
    store.close()


def test_status_without_daemon(tmp_path):
    cfg = load_config(overrides={"daemon.db_path": str(tmp_path / "none.db")})
    assert TestClient(create_app(cfg)).get("/api/status").json()["online"] is False


def test_results_api_reads_simulator_output():
    r = TestClient(create_app()).get("/api/results")
    if r.status_code == 404:
        return  # simulator not run in this checkout
    data = r.json()
    assert any("ours" in name for name in data["main"])
    assert data["calibration"]
