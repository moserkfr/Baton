"""GPU tests for TensorParker: state is identical after park/restore and training continues.

Run explicitly (needs the GPU):  python -m pytest tests/gpu -q
"""
import gc

import pytest
import torch
import torch.nn as nn

from baton.client.tensor_parking import TensorParker

pytestmark = pytest.mark.skipif(not torch.cuda.is_available(), reason="needs a CUDA GPU")


def _clean():
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.synchronize()


def make_student(data_mib: int = 256, seed: int = 0):
    torch.manual_seed(seed)
    model = nn.Sequential(nn.Linear(512, 1024), nn.BatchNorm1d(1024), nn.GELU(), nn.Linear(1024, 10)).cuda()
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    data = torch.randn(data_mib * 2**20 // 4, device="cuda")
    return model, opt, data


def train_step(model, opt, seed):
    g = torch.Generator(device="cuda").manual_seed(seed)
    x = torch.randn(64, 512, device="cuda", generator=g)
    loss = model(x).square().mean()
    opt.zero_grad()
    loss.backward()
    opt.step()
    return loss.item()


def tracked(model, opt, data):
    p = TensorParker()
    p.track_module(model, "model")
    p.track_optimizer(opt, "opt")
    p.track_tensor(data, "data")
    return p


def test_checksum_identical_and_devices_restored():
    model, opt, data = make_student()
    train_step(model, opt, 1)
    p = tracked(model, opt, data)
    before_ck, before_dev = p.checksum(), p.devices()
    assert any(d == "cpu" for k, d in before_dev.items() if k.endswith(".step")), "Adam step lives on CPU"
    p.park()
    assert all(d == "cpu" for d in p.devices().values())
    assert data.device.type == "cpu"           # the user's own variable followed the move
    p.restore()
    assert p.checksum() == before_ck
    assert p.devices() == before_dev           # every tensor back on its ORIGINAL device
    del model, opt, data, p
    _clean()


def test_training_continues_exactly_as_without_parking():
    # reference run: 3 steps, no parking
    m1, o1, _ = make_student(data_mib=1, seed=3)
    ref = [train_step(m1, o1, s) for s in range(3)]
    # same run with a park/restore between every step
    m2, o2, d2 = make_student(data_mib=1, seed=3)
    p = tracked(m2, o2, d2)
    got = []
    for s in range(3):
        got.append(train_step(m2, o2, s))
        p.park()
        p.restore()
    assert got == ref
    assert torch.equal(m1[0].weight, m2[0].weight)
    del m1, o1, m2, o2, d2, p
    _clean()


def test_vram_returns_near_baseline_and_buffers_are_reused():
    _clean()
    base = torch.cuda.memory_reserved()
    base_alloc = torch.cuda.memory_allocated()  # other tests in this process may still hold a little
    model, opt, data = make_student(data_mib=512)
    train_step(model, opt, 0)
    p = tracked(model, opt, data)
    held = torch.cuda.memory_reserved()
    stats = p.park()
    assert torch.cuda.memory_allocated() - base_alloc < 8 * 2**20
    assert torch.cuda.memory_reserved() - base < 0.05 * (held - base)
    assert stats.bytes_moved >= 512 * 2**20
    buffers = {k: v.data_ptr() for k, v in p._pinned.items()}
    p.restore()
    p.park()                                   # second park reuses the same pinned buffers
    assert {k: v.data_ptr() for k, v in p._pinned.items()} == buffers
    p.restore()
    del model, opt, data, p
    _clean()


def test_double_park_and_restore_without_park_are_errors():
    model, opt, data = make_student(data_mib=1)
    p = tracked(model, opt, data)
    with pytest.raises(RuntimeError):
        p.restore()
    p.park()
    with pytest.raises(RuntimeError):
        p.park()
    p.restore()
    del model, opt, data, p
    _clean()
