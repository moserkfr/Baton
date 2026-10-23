"""Benchmark cooperative parking at several sizes and write results/park_bench.md.

    python -m baton.client.bench_parking

For each size: a student-like state (data tensor + MLP + Adam) is parked and restored three times.
The first park includes allocating the pinned host buffers; later parks reuse them. Device memory is
read from NVML (what other users of the GPU actually see), not just from PyTorch's own counters.
"""
from __future__ import annotations

import gc
import statistics
import time

import psutil
import pynvml
import torch
import torch.nn as nn

from ..config import resolve_path
from .tensor_parking import TensorParker

SIZES_GIB = (0.5, 1.0, 2.0)


def device_used_mib(h) -> float:
    return pynvml.nvmlDeviceGetMemoryInfo(h).used / 2**20


def run_size(gib: float, h, ctx_mib: float) -> dict:
    model = nn.Sequential(nn.Linear(1024, 4096), nn.GELU(), nn.Linear(4096, 10)).cuda()
    opt = torch.optim.Adam(model.parameters())
    data = torch.randn(int(gib * 2**30) // 4, device="cuda")
    loss = model(torch.randn(256, 1024, device="cuda")).square().mean()
    loss.backward()
    opt.step()
    p = TensorParker()
    p.track_module(model)
    p.track_optimizer(opt)
    p.track_tensor(data)
    ck = p.checksum()
    gbytes = p.gpu_bytes()
    proc = psutil.Process()
    parks, restores = [], []
    used_running = device_used_mib(h)
    for i in range(3):
        rss0 = proc.memory_info().rss
        parks.append(p.park().seconds)
        used_parked = device_used_mib(h)
        rss_parked = proc.memory_info().rss
        restores.append(p.restore().seconds)
    ok = p.checksum() == ck
    out = {
        "gib": gib, "state_gib": gbytes / 2**30,
        "first_park_s": parks[0], "park_s": statistics.median(parks[1:]),
        "restore_s": statistics.median(restores),
        "vram_running_mib": used_running, "vram_parked_mib": used_parked,
        "left_on_gpu_mib": used_parked - ctx_mib, "ram_while_parked_mib": rss_parked / 2**20,
        "pinned_mib": p.pinned_bytes() / 2**20, "checksum_ok": ok,
    }
    del model, opt, data, p, loss
    gc.collect()
    torch.cuda.empty_cache()
    return out


def main() -> None:
    pynvml.nvmlInit()
    h = pynvml.nvmlDeviceGetHandleByIndex(0)
    base = device_used_mib(h)
    torch.zeros(1, device="cuda")
    torch.cuda.synchronize()
    ctx = device_used_mib(h)
    rows = [run_size(g, h, ctx) for g in SIZES_GIB]
    end = device_used_mib(h)
    lines = [
        "# Cooperative parking benchmark (RTX 3050 Laptop, WDDM)\n",
        f"Device memory before CUDA init: {base:.0f} MiB; after context init: {ctx:.0f} MiB "
        f"(CUDA context ~{ctx - base:.0f} MiB, stays while parked). Generated {time.strftime('%Y-%m-%d %H:%M')}.\n",
        "| Data tensor | State moved (GiB) | First park (s, incl. pinned alloc) | Park (s, reused buffers) | "
        "Restore (s) | VRAM running (MiB) | VRAM parked (MiB) | Left above context (MiB) | Checksum ok |",
        "|" + " --- |" * 9,
    ]
    for r in rows:
        lines.append(f"| {r['gib']} GiB | {r['state_gib']:.2f} | {r['first_park_s']:.2f} | {r['park_s']:.2f} | "
                     f"{r['restore_s']:.2f} | {r['vram_running_mib']:.0f} | {r['vram_parked_mib']:.0f} | "
                     f"{r['left_on_gpu_mib']:.0f} | {r['checksum_ok']} |")
    lines.append(f"\nDevice memory at the end of the benchmark (process still alive): {end:.0f} MiB.")
    out = resolve_path("results/park_bench.md")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
