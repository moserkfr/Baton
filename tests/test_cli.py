"""gpuq CLI against an in-process daemon server (fake monitor, no GPU)."""
import threading
import time

from baton.cli import gpuq
from baton.config import load_config
from baton.daemon.core import Daemon
from baton.daemon.monitor import FakeMonitor
from baton.daemon.server import DaemonServer
from baton.parkers.cooperative import CooperativeParker


def start_server(slots=1):
    cfg = load_config(overrides={"slots.count": slots, "daemon.dry_run": False})
    d = Daemon(cfg, FakeMonitor(), CooperativeParker(), None)
    return d, DaemonServer(d, "127.0.0.1", 0, 0.2).start()


def test_request_status_queue_and_controls(capsys):
    d, srv = start_server(slots=1)
    try:
        port = ["--port", str(srv.port)]
        assert gpuq.main(port + ["request", "--user", "alice", "--exit-after-grant"]) == 0
        assert "got slot 0" in capsys.readouterr().out

        holder = threading.Thread(target=gpuq.main, args=(port + ["request", "--user", "bob"],), daemon=True)
        holder.start()
        time.sleep(0.5)
        waiter = threading.Thread(target=gpuq.main, args=(port + ["request", "--user", "carol",
                                                                  "--exit-after-grant"],), daemon=True)
        waiter.start()
        time.sleep(0.5)
        assert gpuq.main(port + ["status"]) == 0
        out = capsys.readouterr().out
        assert "bob" in out and "carol" in out and "QUEUE:  1. carol" in out
        # bob is a placeholder with no memory: not parkable through the cooperative parker either way
        assert gpuq.main(port + ["resume", "bob"]) == 1
        assert "no parked session" in capsys.readouterr().err
    finally:
        srv.stop()


def test_status_when_daemon_down(capsys, tmp_path):
    rc = gpuq.main(["--port", "1", "status"])
    assert rc in (0, 1)  # 0 if an old snapshot exists in data/, 1 otherwise; never a traceback


def test_snooze_away_mode_why_and_stats(capsys, tmp_path):
    from baton.daemon.state import StateStore

    db = tmp_path / "state.db"
    cfg = load_config(overrides={"slots.count": 1, "daemon.dry_run": False, "daemon.db_path": str(db)})
    store = StateStore(db)
    d = Daemon(cfg, FakeMonitor(), CooperativeParker(), store)

    class H:
        def send(self, msg):
            pass

    sid = d.register(H(), {"user": "alice", "pid": 7})["session"]
    d.on_message(sid, {"type": "report", "mem_reserved": 500 * 2**20, "steps": 1})
    srv = DaemonServer(d, "127.0.0.1", 0, 0.2).start()
    port = ["--port", str(srv.port)]
    try:
        assert gpuq.main(port + ["snooze", "alice"]) == 0
        assert "left today" in capsys.readouterr().out
        assert gpuq.main(port + ["mode", "exam", "--idle", "600", "--grace", "60"]) == 0
        assert "exam (idle window 600" in capsys.readouterr().out
        assert gpuq.main(port + ["mode", "off"]) == 0
        assert "normal" in capsys.readouterr().out
        assert gpuq.main(port + ["away", "alice", "30"]) == 0
        assert "restored just before" in capsys.readouterr().out
        with d.lock:
            d.on_message(sid, {"type": "parked", "park_s": 0.2, "bytes": 2**29})
            d.on_message(sid, {"type": "bye"})
    finally:
        srv.stop()
        store.close()
    import baton.cli.gpuq as mod
    assert gpuq.cmd_why(cfg, type("A", (), {"session": sid})()) == 0
    out = capsys.readouterr().out
    assert "snoozed" in out and "away until" in out and "Parked in 0.20 s" in out
    assert gpuq.cmd_stats(cfg, type("A", (), {"user": None, "days": 1})()) == 0
    assert "alice" in capsys.readouterr().out
    assert mod.parse_when("45") == {"minutes": 45.0} and "until" in mod.parse_when("23:59")
