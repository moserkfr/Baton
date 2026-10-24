"""GPU tests for spill-to-disk, automatic tracking and the Jupyter extension (short, small tensors)."""
import gc
import time

import pytest
import torch
import torch.nn as nn

from baton import jupyter
from baton.client.autotrack import discover
from baton.client.gpuq_client import GpuqClient
from baton.client.tensor_parking import TensorParker
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


def student(seed=0):
    torch.manual_seed(seed)
    model = nn.Sequential(nn.Linear(256, 512), nn.GELU(), nn.Linear(512, 10)).cuda()
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    data = torch.randn(32 * 2**20 // 4, device="cuda")
    return model, opt, data


def train(model, opt, seed):
    g = torch.Generator(device="cuda").manual_seed(seed)
    loss = model(torch.randn(32, 256, device="cuda", generator=g)).square().mean()
    opt.zero_grad()
    loss.backward()
    opt.step()
    return loss.item()


def test_spill_to_disk_frees_ram_and_training_continues_bit_identical(tmp_path):
    ref_model, ref_opt, _ = student()
    model, opt, data = student()
    p = TensorParker()
    p.track_module(model, "m")
    p.track_optimizer(opt, "o")
    p.track_tensor(data, "data")
    for i in range(3):
        assert train(ref_model, ref_opt, i) == train(model, opt, i)
    before = p.checksum()
    shapes = {n: tuple(t.shape) for n, t in p.iter_tensors()}

    p.park()
    assert p.pinned_bytes() > 32 * 2**20
    stats = p.spill(tmp_path / "spill.pt")
    assert (tmp_path / "spill.pt").exists() and stats.bytes_moved > 32 * 2**20
    assert p.pinned_bytes() == 0                                   # host RAM freed
    assert {n: tuple(t.shape) for n, t in p.iter_tensors()} == shapes   # shapes unchanged
    assert all(t.untyped_storage().nbytes() <= 8 for n, t in p.iter_tensors() if n != "o.0.step"
               and not n.endswith(".step"))
    assert p.checksum() == before                                  # checksum reads the file

    p.restore()
    assert not (tmp_path / "spill.pt").exists() and p.spill_path is None
    assert p.checksum() == before
    for i in range(3, 6):                                          # training continues exactly
        assert train(ref_model, ref_opt, i) == train(model, opt, i)
    del model, opt, data, ref_model, ref_opt, p
    gc.collect()
    torch.cuda.empty_cache()


def test_autotrack_finds_notebook_variables_and_holds_them_weakly():
    model, opt, data = student()
    train(model, opt, 0)
    extra = torch.ones(1024, device="cuda")
    ns = {"model": model, "opt": opt, "data": data, "view": data[:100], "_hidden": torch.ones(4, device="cuda"),
          "batch": [extra], "cpu": torch.ones(3), "n": 5}
    found = {(k, n) for k, n, _ in discover(ns)}
    assert ("module", "model") in found and ("optimizer", "opt") in found and ("tensor", "data") in found
    assert ("tensor", "batch[0]") in found
    names = {n for _, n in found}
    assert "view" not in names and "_hidden" not in names and "cpu" not in names

    gq = GpuqClient("auto", verbose=False)                         # tracking needs no daemon
    added = gq.autotrack(ns)
    assert set(added) == {"model", "opt", "data", "batch[0]"}
    assert gq.autotrack(ns) == []                                  # nothing tracked twice
    tracked_bytes = gq.parker.gpu_bytes()
    del ns["data"], ns["view"], data                                 # the view would keep data alive
    gc.collect()
    assert gq.parker.gpu_bytes() == tracked_bytes - 32 * 2**20     # deleted variable is not kept alive


class FakeEvents:
    def __init__(self):
        self.callbacks = {}

    def register(self, name, fn):
        self.callbacks.setdefault(name, []).append(fn)

    def unregister(self, name, fn):
        self.callbacks[name].remove(fn)

    def fire(self, name):
        for fn in list(self.callbacks.get(name, [])):
            fn(None)


class FakeShell:
    def __init__(self):
        self.user_ns, self.events, self.magics = {}, FakeEvents(), {}

    def register_magic_function(self, fn, kind, name):
        self.magics[name] = fn

    def push(self, values):
        self.user_ns.update(values)

    def run_cell(self, code):
        self.events.fire("pre_run_cell")
        exec(code, self.user_ns)
        self.events.fire("post_run_cell")


def test_jupyter_extension_parks_between_cells_and_restores_on_next_cell(monkeypatch, capsys):
    cfg = load_config(overrides={"slots.count": 1, "daemon.dry_run": False, "policy.idle_window_s": 600,
                                 "daemon.poll_interval_s": 0.2})
    daemon = Daemon(cfg, FakeMonitor(), CooperativeParker(), None)
    server = DaemonServer(daemon, "127.0.0.1", 0, 0.2).start()
    monkeypatch.setenv("GPUQ_USER", "nb-student")
    monkeypatch.setenv("GPUQ_PORT", str(server.port))
    ip = FakeShell()
    try:
        jupyter.load_ipython_extension(ip)
        gq = ip.user_ns["gq"]
        assert gq.state == "running" and "gpuq" in ip.magics
        ip.run_cell("import torch\nmodel = torch.nn.Linear(512, 512).cuda()\n"
                    "data = torch.randn(8 * 2**20, device='cuda')\nout = model(data[:512 * 64].view(64, 512))")
        before = gq.checksum()
        assert gq.parker.gpu_bytes() > 32 * 2**20                  # model + data found without track()
        allocated = torch.cuda.memory_allocated()

        with daemon.lock:
            assert daemon.control({"type": "park_now", "session": "nb-student"})["type"] == "ok"
        assert wait_until(lambda: gq.state == "parked")
        assert torch.cuda.memory_allocated() < allocated - 32 * 2**20

        ip.run_cell("total = float(model(data[:512].view(1, 512)).sum().detach())")   # restores before the cell
        assert gq.state == "running" and gq.checksum() == before
        ip.magics["gpuq"]("")
        assert "running as" in capsys.readouterr().out
        ip.magics["gpuq"]("snooze")
        assert wait_until(lambda: any(k == "snooze_ok" for _, k, _ in gq.history))
    finally:
        jupyter.unload_ipython_extension(ip)
        server.stop()
        ip.user_ns.clear()
        gc.collect()
        torch.cuda.empty_cache()
