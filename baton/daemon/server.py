"""The daemon process: TCP control channel + a periodic tick, around daemon.core.Daemon.

    python -m baton.daemon.server                    # real GPU, config.yaml
    python -m baton.daemon.server --dry-run          # only log what it would do
    python -m baton.daemon.server --set policy.idle_window_s=30 --set policy.grace_period_s=10

Threads: one accept loop, one thread per connection (a student session or a one-shot control request),
one tick loop. All of them call into the Daemon under its single lock, so the logic stays sequential.
"""
from __future__ import annotations

import argparse
import socket
import threading
import time
from typing import Any

import yaml

from .. import protocol
from ..config import load_config, resolve_path
from ..parkers.base import make_parker
from .core import Daemon
from .monitor import FakeMonitor, NvmlMonitor
from .state import StateStore


class ConnHandle:
    """Lets the daemon send to one client connection from any thread."""

    def __init__(self, f):
        self.f = f
        self._lock = threading.Lock()

    def send(self, msg: dict[str, Any]) -> None:
        with self._lock:
            protocol.send(self.f, msg)


class DaemonServer:
    def __init__(self, daemon: Daemon, host: str, port: int, poll_interval_s: float):
        self.daemon = daemon
        self.poll = poll_interval_s
        self.srv = socket.create_server((host, port))
        self.port = self.srv.getsockname()[1]
        self._stop = threading.Event()

    def start(self) -> "DaemonServer":
        threading.Thread(target=self._accept_loop, name="accept", daemon=True).start()
        threading.Thread(target=self._tick_loop, name="tick", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            self.srv.close()
        except OSError:
            pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        f = conn.makefile("rw", encoding="utf-8", newline="\n")
        sid = None
        try:
            first = protocol.recv(f)
            if first is None:
                return
            if first.get("type") != "register":  # one-shot control request (CLI, dashboard)
                with self.daemon.lock:
                    reply = self.daemon.control(first)
                protocol.send(f, reply)
                return
            handle = ConnHandle(f)
            with self.daemon.lock:
                reply = self.daemon.register(handle, first)
                sid = reply["session"]
                handle.send(reply)   # under the lock: the reply goes out before any later grant/park
            while True:
                msg = protocol.recv(f)
                if msg is None:
                    break
                with self.daemon.lock:
                    self.daemon.on_message(sid, msg)
        except (OSError, ValueError):
            pass
        finally:
            if sid is not None:
                with self.daemon.lock:
                    self.daemon.disconnect(sid, reason="connection closed")
            try:
                conn.close()
            except OSError:
                pass

    def _tick_loop(self) -> None:
        while not self._stop.wait(self.poll):
            with self.daemon.lock:
                self.daemon.tick()
                status = self.daemon.status()
            if self.daemon.store:
                self.daemon.store.save_snapshot(status["time"], status)


def build_daemon(cfg: dict[str, Any], fake_monitor: bool = False, echo: bool = True) -> Daemon:
    monitor = FakeMonitor() if fake_monitor else NvmlMonitor(cfg["daemon"].get("gpu_index", 0))
    store = StateStore(resolve_path(cfg["daemon"]["db_path"]))
    return Daemon(cfg, monitor, make_parker(cfg["parker"]["kind"]), store, echo=echo)


def parse_overrides(items: list[str]) -> dict[str, Any]:
    out = {}
    for item in items:
        key, _, value = item.partition("=")
        out[key] = yaml.safe_load(value)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Baton daemon")
    ap.add_argument("--config", default=None)
    ap.add_argument("--dry-run", action="store_true", help="log decisions, never warn or park")
    ap.add_argument("--fake-monitor", action="store_true", help="no NVML (for machines without a GPU)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override a config key")
    args = ap.parse_args(argv)
    overrides = parse_overrides(args.set)
    if args.dry_run:
        overrides["daemon.dry_run"] = True
    cfg = load_config(args.config, overrides)
    daemon = build_daemon(cfg, args.fake_monitor)
    d = cfg["daemon"]
    server = DaemonServer(daemon, d["host"], d["port"], d["poll_interval_s"]).start()
    p = cfg["policy"]
    print(f"[daemon] listening on {d['host']}:{server.port}  slots={cfg['slots']['count']}x{cfg['slots']['cap_gib']} GiB "
          f"(fraction {daemon.slots.cap_fraction:.3f})  idle={p['idle_window_s']}s grace={p['grace_period_s']}s "
          f"parker={daemon.parker.name} dry_run={daemon.dry_run}", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        daemon.monitor.close()


if __name__ == "__main__":
    main()
