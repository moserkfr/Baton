"""Jupyter/IPython extension: GPU sharing with no changes to the student's code.

    %load_ext baton.jupyter       # first cell of the notebook; waits for a GPU slot
    ... ordinary PyTorch cells ...
    %gpuq                                 # status: slot, state, what's tracked, latest notifications
    %gpuq snooze                          # "I'm thinking, not idle"
    %gpuq away 45                         # park now, restore just before I'm back (minutes, or 15:30)

How it works: every cell runs as one `step` of the helper library. Before a cell, the extension waits
for any park to finish (or asks for the GPU back and restores, if the session was parked). After the
cell, it scans the notebook's variables for models, optimizers and GPU tensors (`autotrack`) so the
next park knows what to move. A cell is never parked halfway: parking waits for the cell to end.

Settings come from environment variables: GPUQ_USER (default: your login name), GPUQ_NAME,
GPUQ_HOST, GPUQ_PORT.
"""
from __future__ import annotations

import getpass
import os
import time
from datetime import datetime
from typing import Any

from .client.gpuq_client import DEFAULT_HOST, DEFAULT_PORT, GpuqClient

_hooks: "NotebookHooks | None" = None


class NotebookHooks:
    def __init__(self, ip: Any, client: GpuqClient):
        self.ip, self.client = ip, client
        self.in_cell = False

    def register(self) -> None:
        self.ip.events.register("pre_run_cell", self.pre_run_cell)
        self.ip.events.register("post_run_cell", self.post_run_cell)

    def unregister(self) -> None:
        for name, fn in (("pre_run_cell", self.pre_run_cell), ("post_run_cell", self.post_run_cell)):
            try:
                self.ip.events.unregister(name, fn)
            except ValueError:
                pass

    def pre_run_cell(self, info: Any = None) -> None:
        if not self.in_cell:
            self.client.begin_step()      # restores first if the session was parked
            self.in_cell = True

    def post_run_cell(self, result: Any = None) -> None:
        try:
            self.client.autotrack(self.ip.user_ns)
        finally:
            if self.in_cell:              # not set for the cell that loaded the extension
                self.in_cell = False
                self.client.end_step()


def _parse_until(arg: str) -> dict[str, float]:
    """'45' -> 45 minutes; '15:30' -> today (or tomorrow) at 15:30."""
    if ":" in arg:
        hh, mm = (int(x) for x in arg.split(":", 1))
        target = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
        ts = target.timestamp()
        return {"until": ts if ts > time.time() else ts + 86400}
    return {"minutes": float(arg)}


def gpuq_magic(line: str) -> None:
    if _hooks is None:
        print("gpuq is not loaded; run %load_ext baton.jupyter")
        return
    gq = _hooks.client
    parts = line.split()
    cmd = parts[0] if parts else "status"
    if cmd == "snooze":
        gq.snooze()
    elif cmd == "away":
        if len(parts) < 2:
            print("usage: %gpuq away 45   or   %gpuq away 15:30")
            return
        gq.away(**_parse_until(parts[1]))
    elif cmd == "track":
        print("now tracking:", ", ".join(gq.autotrack(_hooks.ip.user_ns)) or "nothing new")
    else:
        gib = gq.parker.gpu_bytes() / 2**30
        print(f"gpuq: {gq.state} as {gq.session} on slot {gq.slot}; tracking {gib:.2f} GiB of GPU tensors")
        for n in gq.notifications[-3:]:
            print(f"  - {n.get('title')}: {n.get('body')}")


def load_ipython_extension(ip: Any) -> None:
    global _hooks
    if _hooks is not None:
        print("gpuq already loaded")
        return
    user = os.environ.get("GPUQ_USER") or getpass.getuser()
    client = GpuqClient(user=user, name=os.environ.get("GPUQ_NAME") or user,
                        host=os.environ.get("GPUQ_HOST", DEFAULT_HOST),
                        port=int(os.environ.get("GPUQ_PORT", DEFAULT_PORT)))
    print("gpuq: asking for a GPU slot...")
    client.connect()
    _hooks = NotebookHooks(ip, client)
    _hooks.register()
    ip.register_magic_function(gpuq_magic, "line", "gpuq")
    ip.push({"gq": client})
    print(f"gpuq: ready on slot {client.slot}. Idle sessions are parked safely and come back when you run a cell.")


def unload_ipython_extension(ip: Any) -> None:
    global _hooks
    if _hooks is not None:
        _hooks.unregister()
        _hooks.client.close()
        _hooks = None
