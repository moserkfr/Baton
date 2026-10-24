"""The four simulated students of the mini-lab. Each runs as its own process.

    python -m baton.minilab.students A --port 7311

| Student | Workload (synthetic data, no downloads)        | GPU memory |
|---------|-------------------------------------------------|------------|
| A       | ResNet-18-style CNN on CIFAR-sized images, SGD  | ~0.6 GB    |
| B       | ~40M-parameter GPT (causal transformer), Adam   | ~0.9 GB    |
| C       | MLP + a 1 GiB data tensor held on the GPU        | ~1.4 GB    |
| D       | heavy job: wide MLP (67M params) on a large batch, Adam | ~1.9 GB |

A student is driven by commands on stdin, one per line, so the demo script can play a story:
    work <seconds> [pause]  run training steps for that long, or until the next command arrives
    touch                   enter one empty step (forces a restore if parked), then report a checksum
    checksum                report the checksum of all tracked state (works while parked, too)
    exit                    leave cleanly
Everything the student does is printed as one JSON object per line on stdout.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import queue
import sys
import threading
import time

print(json.dumps({"event": "pid", "pid": os.getpid()}), flush=True)  # before the slow torch import

import torch
import torch.nn as nn
import torch.nn.functional as F

from ..client.gpuq_client import GpuqClient


def emit(who: str, event: str, **kw) -> None:
    print(json.dumps({"t": time.time(), "who": who, "event": event, **kw}), flush=True)


# ---------------------------------------------------------------- models

class BasicBlock(nn.Module):
    def __init__(self, cin: int, cout: int, stride: int):
        super().__init__()
        self.c1 = nn.Conv2d(cin, cout, 3, stride, 1, bias=False)
        self.b1 = nn.BatchNorm2d(cout)
        self.c2 = nn.Conv2d(cout, cout, 3, 1, 1, bias=False)
        self.b2 = nn.BatchNorm2d(cout)
        self.short = nn.Sequential()
        if stride != 1 or cin != cout:
            self.short = nn.Sequential(nn.Conv2d(cin, cout, 1, stride, bias=False), nn.BatchNorm2d(cout))

    def forward(self, x):
        out = F.relu(self.b1(self.c1(x)))
        return F.relu(self.b2(self.c2(out)) + self.short(x))


class ResNet18(nn.Module):
    """ResNet-18 adapted to 32x32 inputs (CIFAR style), ~11M parameters."""

    def __init__(self, classes: int = 10):
        super().__init__()
        self.stem = nn.Sequential(nn.Conv2d(3, 64, 3, 1, 1, bias=False), nn.BatchNorm2d(64), nn.ReLU())
        layers, cin = [], 64
        for cout, stride in ((64, 1), (128, 2), (256, 2), (512, 2)):
            layers += [BasicBlock(cin, cout, stride), BasicBlock(cout, cout, 1)]
            cin = cout
        self.layers = nn.Sequential(*layers)
        self.head = nn.Linear(512, classes)

    def forward(self, x):
        x = self.layers(self.stem(x))
        return self.head(F.adaptive_avg_pool2d(x, 1).flatten(1))


class TinyGPT(nn.Module):
    """Causal transformer language model: d=512, 12 layers, vocab 8192 -> ~42M parameters."""

    def __init__(self, vocab: int = 8192, d: int = 512, layers: int = 12, heads: int = 8, ctx: int = 128):
        super().__init__()
        self.tok = nn.Embedding(vocab, d)
        self.pos = nn.Embedding(ctx, d)
        layer = nn.TransformerEncoderLayer(d, heads, 4 * d, dropout=0.0, batch_first=True, norm_first=True)
        self.blocks = nn.TransformerEncoder(layer, layers, enable_nested_tensor=False)
        self.norm = nn.LayerNorm(d)
        self.head = nn.Linear(d, vocab, bias=False)
        self.head.weight = self.tok.weight  # tied embeddings, as in GPT-2
        nn.init.normal_(self.tok.weight, std=0.02)  # GPT-2 init; std 1 makes tied logits explode
        nn.init.normal_(self.pos.weight, std=0.02)
        self.register_buffer("mask", torch.triu(torch.full((ctx, ctx), float("-inf")), 1), persistent=False)

    def forward(self, idx):
        T = idx.shape[1]
        x = self.tok(idx) + self.pos(torch.arange(T, device=idx.device))
        x = self.blocks(x, mask=self.mask[:T, :T], is_causal=True)
        return self.head(self.norm(x))


# ---------------------------------------------------------------- workloads

class Workload:
    """Model + optimizer + synthetic data, and one training step."""

    def __init__(self, who: str, gq: GpuqClient, seed: int = 0):
        torch.manual_seed(seed)
        g = torch.Generator(device="cuda").manual_seed(seed)
        self.who, self.step_i = who, 0
        dev = "cuda"
        if who == "A":
            self.model = ResNet18().to(dev)
            self.opt = torch.optim.SGD(self.model.parameters(), lr=0.05, momentum=0.9)
            self.x = torch.randn(4096, 3, 32, 32, device=dev, generator=g)     # CIFAR-sized images
            self.y = torch.randint(0, 10, (4096,), device=dev, generator=g)
            self.batch = 64
        elif who == "B":
            self.model = TinyGPT().to(dev)
            self.opt = torch.optim.Adam(self.model.parameters(), lr=1e-3)
            # learnable synthetic text: each sequence counts up from a random start with a random stride,
            # so the loss visibly falls and a resumed run can be seen continuing the same curve
            start = torch.randint(0, 8192, (2048, 1), device=dev, generator=g)
            stride = torch.randint(1, 8, (2048, 1), device=dev, generator=g)
            self.x = (start + stride * torch.arange(129, device=dev)) % 8192
            self.y = None
            self.batch = 8
        elif who == "C":
            self.model = nn.Sequential(nn.Linear(1024, 2048), nn.ReLU(), nn.Linear(2048, 10)).to(dev)
            self.opt = torch.optim.Adam(self.model.parameters(), lr=1e-3)
            self.x = torch.randn(2**30 // 4 // 1024, 1024, device=dev, generator=g)  # 1 GiB of features
            self.y = torch.randint(0, 10, (self.x.shape[0],), device=dev, generator=g)
            self.batch = 512
        elif who == "D":
            self.model = nn.Sequential(nn.Linear(4096, 8192), nn.ReLU(), nn.Linear(8192, 4096), nn.ReLU(),
                                       nn.Linear(4096, 10)).to(dev)
            self.opt = torch.optim.Adam(self.model.parameters(), lr=1e-4)
            self.x = torch.randn(32768, 4096, device=dev, generator=g)          # 0.5 GiB of features
            self.y = torch.randint(0, 10, (32768,), device=dev, generator=g)
            self.batch = 2048
        else:
            raise ValueError(who)
        gq.track(self.model, "model")
        gq.track(self.opt, "opt")
        gq.track(self.x, "x")
        if self.y is not None:
            gq.track(self.y, "y")

    def step(self) -> float:
        n = self.x.shape[0]
        i = (self.step_i * self.batch) % (n - self.batch)
        self.step_i += 1
        if self.who == "B":
            seq = self.x[i:i + self.batch]
            logits = self.model(seq[:, :-1])
            loss = F.cross_entropy(logits.reshape(-1, logits.shape[-1]), seq[:, 1:].reshape(-1))
        else:
            loss = F.cross_entropy(self.model(self.x[i:i + self.batch]), self.y[i:i + self.batch])
        self.opt.zero_grad(set_to_none=False)  # keep grad tensors so they are tracked and parked too
        loss.backward()
        self.opt.step()
        return loss.item()


# ---------------------------------------------------------------- main loop

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("who", choices=list("ABCD"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7311)
    ap.add_argument("--slot-gib", type=float, default=2.2)
    args = ap.parse_args(argv)
    who = args.who

    def on_message(msg):
        emit(who, "daemon_" + msg.get("type", "?"), **{k: v for k, v in msg.items() if k != "type"})

    gq = GpuqClient(f"student-{who}", slot_mem_gib=args.slot_gib, host=args.host, port=args.port,
                    name=who, on_message=on_message)
    t0 = time.time()
    emit(who, "connecting")
    gq.connect()
    emit(who, "granted", slot=gq.slot, waited_s=time.time() - t0)
    w = Workload(who, gq, seed=ord(who))
    with gq.step():
        loss = w.step()
    emit(who, "ready", mem_reserved_mib=torch.cuda.memory_reserved() / 2**20, loss=loss,
         params_m=sum(p.numel() for p in w.model.parameters()) / 1e6)

    commands: "queue.Queue[str]" = queue.Queue()

    def read_stdin():
        for line in sys.stdin:
            commands.put(line)
        commands.put("exit")

    threading.Thread(target=read_stdin, daemon=True).start()
    while True:
        cmd = commands.get().split()
        if not cmd:
            continue
        if cmd[0] == "work":
            secs = float(cmd[1])
            pause = float(cmd[2]) if len(cmd) > 2 else 0.0
            end, losses, t_start = time.time() + secs, [], time.time()
            while time.time() < end and commands.empty():  # a new command interrupts the work
                t_step = time.time()
                with gq.step():
                    losses.append(w.step())
                if time.time() - t_step > 0.5:  # this step had to wait for a restore
                    emit(who, "step_waited", seconds=time.time() - t_step, loss=losses[-1])
                if pause:
                    time.sleep(pause)
            emit(who, "worked", steps=len(losses), first_loss=losses[0] if losses else math.nan,
                 last_loss=losses[-1] if losses else math.nan, seconds=time.time() - t_start,
                 step_total=w.step_i)
        elif cmd[0] == "touch":
            t1 = time.time()
            with gq.step():
                pass
            emit(who, "touched", waited_s=time.time() - t1, checksum=gq.checksum(), state=gq.state)
        elif cmd[0] == "checksum":
            emit(who, "checksum", checksum=gq.checksum(), state=gq.state,
                 devices=sorted(set(gq.parker.devices().values())))
        elif cmd[0] == "exit":
            break
    emit(who, "exit", steps=w.step_i)
    gq.close()


if __name__ == "__main__":
    main()
