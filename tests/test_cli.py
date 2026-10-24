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
