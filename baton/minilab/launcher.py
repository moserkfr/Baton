"""Start, talk to and clean up the mini-lab processes (daemon + students).

Each child prints its own PID first (`os.getpid()`). On Windows a venv's python.exe is a launcher
process, so the PID returned by Popen is not the process that owns the GPU memory; we record both in
data/run/pids.txt and stop both at cleanup.
"""
from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import psutil

from ..config import PROJECT_ROOT
from ..runtime import record_pid


@dataclass
class Child:
    label: str
    popen: subprocess.Popen
    real_pid: int | None = None
    lines: "queue.Queue[dict[str, Any]]" = field(default_factory=queue.Queue)

    def send(self, command: str) -> None:
        self.popen.stdin.write(command + "\n")
        self.popen.stdin.flush()

    def wait_event(self, event: str, timeout: float = 60.0) -> dict[str, Any]:
        """Block until this child emits a JSON event with this name."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                ev = self.lines.get(timeout=max(0.01, deadline - time.time()))
            except queue.Empty:
                break
            if ev.get("event") == event:
                return ev
        raise TimeoutError(f"{self.label}: no {event!r} within {timeout:.0f} s")


class MiniLab:
    """Owns every process of one mini-lab run. Use as a context manager so cleanup always happens."""

    def __init__(self, log: Callable[[str, str], None]):
        self.log = log            # log(source, text)
        self.children: list[Child] = []

    def spawn(self, label: str, args: list[str], env: dict[str, str] | None = None) -> Child:
        p = subprocess.Popen([sys.executable, "-u", *args], cwd=PROJECT_ROOT, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                             encoding="utf-8", env={**os.environ, **(env or {})})
        record_pid(p.pid, f"{label} (launcher)")
        child = Child(label, p)
        self.children.append(child)
        threading.Thread(target=self._pump, args=(child,), daemon=True).start()
        return child

    def _pump(self, child: Child) -> None:
        for line in child.popen.stdout:
            line = line.rstrip()
            if not line:
                continue
            ev = None
            if line.startswith("{"):
                try:
                    ev = json.loads(line)
                except ValueError:
                    ev = None
            if ev is not None:
                if ev.get("event") == "pid":
                    child.real_pid = int(ev["pid"])
                    if child.real_pid != child.popen.pid:
                        record_pid(child.real_pid, child.label)
                    continue
                child.lines.put(ev)
                self.log(child.label, json.dumps({k: v for k, v in ev.items() if k not in ("t", "who")}))
            else:
                self.log(child.label, line)

    def stop_all(self, timeout: float = 8.0) -> None:
        """Ask nicely (stdin exit), then terminate every process we started, launchers and real PIDs."""
        for c in self.children:
            if c.popen.poll() is None:
                try:
                    c.send("exit")
                except OSError:
                    pass
        deadline = time.time() + timeout
        for c in self.children:
            try:
                c.popen.wait(max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                pass
        for c in self.children:
            for pid in {c.popen.pid, c.real_pid} - {None}:
                try:
                    proc = psutil.Process(pid)
                    proc.terminate()
                    proc.wait(3)
                except psutil.NoSuchProcess:
                    pass
                except psutil.TimeoutExpired:
                    proc.kill()

    def __enter__(self) -> "MiniLab":
        return self

    def __exit__(self, *exc) -> None:
        self.stop_all()


def wait_for_port(host: str, port: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection((host, port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"nothing listening on {host}:{port}")


def port_is_free(host: str, port: int) -> bool:
    try:
        socket.create_server((host, port)).close()
        return True
    except OSError:
        return False
