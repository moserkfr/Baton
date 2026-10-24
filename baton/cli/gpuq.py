"""gpuq: command-line control for Baton (no torch import, starts instantly).

    python -m baton.cli.gpuq status
    python -m baton.cli.gpuq request --user alice     # wait for a slot and hold it until Ctrl+C
    python -m baton.cli.gpuq park-now B               # session id or user/name (demo control)
    python -m baton.cli.gpuq resume B
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

from .. import protocol
from ..config import load_config, resolve_path
from ..daemon.state import read_snapshot


def fmt_status(st: dict) -> str:
    lines = []
    gpu = st.get("gpu") or {}
    lines.append(f"GPU {gpu.get('used_mib', '?')}/{gpu.get('total_mib', '?')} MiB, util {gpu.get('util', '?')}%, "
                 f"parker={st.get('parker')}{'  DRY RUN' if st.get('dry_run') else ''}")
    lines.append("")
    lines.append(f"{'SLOT':<5} {'HOLDER':<14} {'STATE':<10} {'MEM MiB':>8} {'UTIL':>5}  NOTE")
    for slot in st["slots"]:
        h = slot["holder"]
        if not h:
            lines.append(f"{slot['slot']:<5} {'-':<14} {'free':<10}")
            continue
        note = ""
        if h["state"] == "warned":
            note = f"parking in {h['park_in_s']:.0f} s"
        elif h.get("idle_s") is not None and h["state"] == "idle":
            note = f"no GPU work for {h['idle_s']:.0f} s"
        lines.append(f"{slot['slot']:<5} {h['name'] + ' (' + h['sid'] + ')':<14} {h['state']:<10} "
                     f"{h['mem_mib']:>8} {h['util']:>4}%  {note}")
    parked = [s for s in st["sessions"] if s["state"] == "parked"]
    if parked:
        lines.append("")
        lines.append("PARKED: " + ", ".join(f"{s['name']} ({s['sid']}, {s['parked_s']:.0f} s)" for s in parked))
    lines.append("")
    if st["queue"]:
        lines.append("QUEUE:  " + "  ".join(f"{i + 1}. {q['user']}{' [returning]' if q['returning'] else ''} "
                                            f"({q['waiting_s']:.0f} s)" for i, q in enumerate(st["queue"])))
    else:
        lines.append("QUEUE:  empty")
    s = st["stats"]
    lines.append(f"STATS:  reclaimed {s['reclaimed_gpu_s']:.0f} GPU-seconds, {s['parks']} parks, "
                 f"{s.get('restores', 0)} restores, {len(s['receipts'])} receipts")
    return "\n".join(lines)


def cmd_status(cfg: dict, args) -> int:
    d = cfg["daemon"]
    try:
        reply = protocol.request(d["host"], d["port"], {"type": "status"})
        st = reply["status"]
    except OSError:
        st = read_snapshot(resolve_path(d["db_path"]))
        if st is None:
            print("daemon not running and no snapshot found", file=sys.stderr)
            return 1
        print(f"(daemon unreachable; last snapshot {time.time() - st['time']:.0f} s old)")
    print(fmt_status(st))
    return 0


def cmd_control(cfg: dict, kind: str, session: str) -> int:
    d = cfg["daemon"]
    try:
        reply = protocol.request(d["host"], d["port"], {"type": kind, "session": session})
    except OSError as e:
        print(f"daemon not reachable: {e}", file=sys.stderr)
        return 1
    if not reply or reply.get("type") == "error":
        print(f"error: {(reply or {}).get('error', 'no reply')}", file=sys.stderr)
        return 1
    print(f"ok: {reply['session']} is now {reply['state']}")
    return 0


def cmd_request(cfg: dict, args) -> int:
    """Register a placeholder session, wait for a slot, hold it until Ctrl+C (or --exit-after-grant)."""
    d = cfg["daemon"]
    sock, f = protocol.connect(d["host"], d["port"])
    protocol.send(f, {"type": "register", "user": args.user, "pid": os.getpid(), "name": args.name or args.user,
                      "slot_mem_gib": args.mem})
    reply = protocol.recv(f)
    if not reply:
        print("no reply from daemon", file=sys.stderr)
        return 1
    stop = threading.Event()

    def heartbeat():  # the daemon drops sessions that stay silent
        while not stop.wait(1.0):
            try:
                protocol.send(f, {"type": "report", "mem_reserved": 0, "steps": 0, "state": "cli"})
            except OSError:
                return

    threading.Thread(target=heartbeat, daemon=True).start()
    t0 = time.time()
    if reply["state"] == "queued":
        print(f"queued as {reply['session']}; waiting for a slot...", flush=True)
        while True:
            msg = protocol.recv(f)
            if msg is None:
                print("daemon went away", file=sys.stderr)
                return 1
            if msg.get("type") == "grant":
                reply["slot"] = msg["slot"]
                break
    print(f"got slot {reply['slot']} as {reply['session']} after {time.time() - t0:.1f} s "
          f"(memory fraction {reply['cap_fraction']:.3f})", flush=True)
    try:
        if not args.exit_after_grant:
            print("holding the slot; Ctrl+C to release", flush=True)
            while protocol.recv(f) is not None:
                pass
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        try:
            protocol.send(f, {"type": "bye"})
        except OSError:
            pass
        sock.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gpuq", description="Baton control")
    ap.add_argument("--port", type=int, default=None, help="daemon port (default from config.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="slots, queue, parked sessions, stats")
    r = sub.add_parser("request", help="wait for a GPU slot and hold it")
    r.add_argument("--user", default=os.environ.get("USERNAME") or os.environ.get("USER") or "cli")
    r.add_argument("--name", default=None)
    r.add_argument("--mem", type=float, default=None, help="requested slot memory, GiB")
    r.add_argument("--exit-after-grant", action="store_true")
    for name, helptext in (("resume", "restore a parked session"), ("park-now", "park a session immediately")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("session", help="session id (s3) or user/name")
    args = ap.parse_args(argv)
    cfg = load_config(overrides={"daemon.port": args.port} if args.port else None)
    if args.cmd == "status":
        return cmd_status(cfg, args)
    if args.cmd == "request":
        return cmd_request(cfg, args)
    return cmd_control(cfg, "park_now" if args.cmd == "park-now" else "resume", args.session)


if __name__ == "__main__":
    sys.exit(main())
