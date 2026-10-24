"""Daemon server over real TCP with real gpuq_clients (one process, fake NVML, short timings)."""
import threading
import time

import pytest
import torch

from baton.client.gpuq_client import GpuqClient
from baton.config import load_config
from baton.daemon.core import Daemon
from baton.daemon.monitor import FakeMonitor
from baton.daemon.server import DaemonServer
from baton.parkers.cooperative import CooperativeParker

pytestmark = pytest.mark.skipif(not torch.cuda.is_available(), reason="needs a CUDA GPU")


def wait_until(cond, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_idle_client_parked_queued_client_served_owner_restored():
    cfg = load_config(overrides={"slots.count": 1, "policy.idle_window_s": 1.5, "policy.grace_period_s": 1.0,
                                 "policy.idle_display_s": 0.5, "daemon.poll_interval_s": 0.2,
                                 "daemon.dry_run": False, "policy.min_memory_mib": 1})
    daemon = Daemon(cfg, FakeMonitor(), CooperativeParker(), None)
    server = DaemonServer(daemon, "127.0.0.1", 0, 0.2).start()
    try:
        a = GpuqClient("A", port=server.port, verbose=False, report_interval=0.2).connect(timeout=5)
        x = a.track(torch.randn(16 * 2**20 // 4, device="cuda"))
        with a.step():
            x.mul_(1.0)
        ck = a.checksum()

        b = GpuqClient("B", port=server.port, verbose=False, report_interval=0.2)
        tb = threading.Thread(target=b.connect, kwargs={"timeout": 15})
        tb.start()
        assert wait_until(lambda: b.state == "queued")
        # A does nothing: warned, parked, and B gets the slot
        assert wait_until(lambda: a.state == "parked", 10)
        tb.join(10)
        assert b.state == "running" and b.slot == 0
        assert any(k == "warn" for _, k, _ in a.history)
        y = b.track(torch.ones(1024, device="cuda"))

        # A comes back while B holds the only slot: A blocks until B leaves
        done = threading.Event()

        def a_returns():
            with a.step():
                x.mul_(1.0)
            done.set()

        threading.Thread(target=a_returns, daemon=True).start()
        time.sleep(0.5)
        assert not done.is_set()
        b.close()
        assert done.wait(10)
        assert a.state == "running" and a.checksum() == ck
        with daemon.lock:
            st = daemon.status()
        assert st["stats"]["parks"] == 1 and st["stats"]["restores"] == 1
        assert st["stats"]["receipts"][0]["beneficiary"] == "B"
        a.close()
        del x, y
    finally:
        server.stop()
