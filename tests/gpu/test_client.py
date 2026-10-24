"""gpuq_client against a scripted fake daemon (same process, real GPU)."""
import gc
import os
import queue
import socket
import threading
import time

import pytest
import torch
import torch.nn as nn

from baton import protocol
from baton.client.gpuq_client import GpuqClient

pytestmark = pytest.mark.skipif(not torch.cuda.is_available(), reason="needs a CUDA GPU")


class FakeDaemon:
    """Accepts one client; records what it sends; lets the test push messages to it."""

    def __init__(self, start_queued=False):
        self.srv = socket.create_server(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        self.inbox: "queue.Queue[dict]" = queue.Queue()
        self.start_queued = start_queued
        self.f = None
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.srv.accept()
        self.conn = conn
        self.f = conn.makefile("rw", encoding="utf-8", newline="\n")
        reg = protocol.recv(self.f)
        self.inbox.put(reg)
        state = "queued" if self.start_queued else "running"
        protocol.send(self.f, {"type": "registered", "session": "s1", "state": state,
                               "slot": None if self.start_queued else 0, "cap_fraction": 0.367})
        while True:
            try:
                msg = protocol.recv(self.f)
            except OSError:  # kill() shut the socket down under us
                return
            if msg is None:
                return
            self.inbox.put(msg)

    def kill(self):
        self.conn.shutdown(socket.SHUT_RDWR)
        self.conn.close()
        self.srv.close()

    def push(self, msg):
        protocol.send(self.f, msg)

    def expect(self, kind, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self.inbox.get(timeout=deadline - time.time())
            except queue.Empty:
                break
            if msg["type"] == kind:
                return msg
        raise AssertionError(f"no {kind!r} message")


def test_register_queue_then_grant_reports_pid_and_memory():
    d = FakeDaemon(start_queued=True)
    c = GpuqClient("alice", slot_mem_gib=2.2, port=d.port, verbose=False, report_interval=0.2)
    t = threading.Thread(target=c.connect)
    t.start()
    reg = d.expect("register")
    assert reg["pid"] == os.getpid() and reg["user"] == "alice"
    time.sleep(0.3)
    assert c.state == "queued"
    assert d.expect("report")["state"] == "queued"  # keeps reporting while queued (else: dropped as silent)
    d.push({"type": "grant", "slot": 1, "cap_fraction": 0.367})
    t.join(5)
    assert c.state == "running" and c.slot == 1
    x = c.track(torch.ones(2**20, device="cuda"))
    rep = d.expect("report")
    for _ in range(10):   # a report already in flight may predate the allocation
        if rep["mem_reserved"] >= x.numel() * 4:
            break
        rep = d.expect("report")
    assert rep["mem_reserved"] >= x.numel() * 4
    c.close()
    del x
    gc.collect()
    torch.cuda.empty_cache()


def test_park_waits_for_step_then_resume_on_next_step():
    d = FakeDaemon()
    c = GpuqClient("bob", port=d.port, verbose=False, report_interval=0.2).connect()
    torch.manual_seed(0)
    model = c.track(nn.Linear(256, 256).cuda())
    opt = c.track(torch.optim.Adam(model.parameters()))
    data = c.track(torch.randn(64 * 2**20 // 4, device="cuda"))

    def train():
        loss = model(torch.ones(8, 256, device="cuda")).square().mean()
        opt.zero_grad()
        loss.backward()
        opt.step()

    with c.step():
        train()
    ck = c.checksum()

    # park arrives while a step is running: it must wait until the step ends
    with c.step():
        d.push({"type": "park"})
        time.sleep(0.3)
        assert not c.parker.parked
        train()
    parked = d.expect("parked")
    assert c.parker.parked and data.device.type == "cpu"
    assert parked["bytes"] >= 64 * 2**20
    ck_parked = c.checksum()

    # the owner returns: step() sends `request` and blocks until `resume`
    done = threading.Event()

    def owner_returns():
        with c.step():
            train()
        done.set()

    threading.Thread(target=owner_returns, daemon=True).start()
    d.expect("request")
    assert not done.is_set()
    d.push({"type": "resume", "slot": 0, "cap_fraction": 0.367})
    resumed = d.expect("resumed")
    assert done.wait(5)
    assert resumed["slot"] == 0 and data.device.type == "cuda"
    assert ck != ck_parked  # the step inside the park window changed state before parking
    c.close()
    del model, opt, data
    gc.collect()
    torch.cuda.empty_cache()


def test_checksum_survives_park_resume_exactly():
    d = FakeDaemon()
    c = GpuqClient("carol", port=d.port, verbose=False).connect()
    c.track(torch.randn(2**20, device="cuda"))
    ck = c.checksum()
    d.push({"type": "park"})
    d.expect("parked")
    d.push({"type": "resume", "slot": 1})
    d.expect("resumed")
    assert c.checksum() == ck
    c.close()


def test_fail_open_when_daemon_dies_while_parked():
    d = FakeDaemon()
    c = GpuqClient("dave", port=d.port, verbose=False).connect()
    x = c.track(torch.randn(1024, device="cuda"))
    d.push({"type": "park"})
    d.expect("parked")
    d.kill()
    deadline = time.time() + 5
    while c.state != "disconnected" and time.time() < deadline:
        time.sleep(0.05)
    with c.step():  # does not block forever
        y = x * 2
    assert x.device.type == "cuda" and y.device.type == "cuda"
    c.close()


def test_failed_restore_raises_in_step_instead_of_hanging():
    d = FakeDaemon()
    c = GpuqClient("erin", port=d.port, verbose=False, report_interval=0.2).connect()
    c.track(torch.ones(1024, device="cuda"), "x")
    d.push({"type": "park"})
    d.expect("parked")
    real_restore = c.parker.restore
    c.parker.restore = lambda: (_ for _ in ()).throw(torch.cuda.OutOfMemoryError("no room"))
    errors = []

    def step():
        try:
            with c.step():
                pass
        except RuntimeError as e:
            errors.append(e)

    t = threading.Thread(target=step)
    t.start()
    d.expect("request")
    d.push({"type": "resume", "slot": 0, "cap_fraction": 0.367})
    assert d.expect("resume_failed")["error"]
    t.join(5)
    assert not t.is_alive() and errors and c.parker.parked
    c.parker.restore = real_restore                   # the next step asks again and succeeds
    t = threading.Thread(target=step)
    t.start()
    d.expect("request")
    d.push({"type": "resume", "slot": 0, "cap_fraction": 0.367})
    d.expect("resumed")
    t.join(5)
    assert not t.is_alive() and not c.parker.parked and len(errors) == 1
    c.close()
