"""gpuq: command-line control for Baton (no torch import, starts instantly).

    python -m baton.cli.gpuq status
    python -m baton.cli.gpuq request --user alice     # wait for a slot and hold it until Ctrl+C
    python -m baton.cli.gpuq park-now B               # session id or user/name (demo control)
    python -m baton.cli.gpuq resume B
    python -m baton.cli.gpuq snooze B                 # "I'm thinking, not idle"
    python -m baton.cli.gpuq away B 45                # park now, restore in 45 min (or 15:30)
    python -m baton.cli.gpuq why s3                   # why was this session parked? (timeline)
    python -m baton.cli.gpuq stats [--user alice]     # impact stats / leaderboard
    python -m baton.cli.gpuq mode deadline --idle 600 --grace 120 [--minutes 600] | mode off
    python -m baton.cli.gpuq dryrun-report            # what a dry run would have reclaimed
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

from .. import protocol
from ..config import load_config, resolve_path
from ..daemon.dryrun import dryrun_report
from ..daemon.explain import timeline
from ..daemon.state import read_session_events, read_snapshot
from ..daemon.stats import leaderboard


def fmt_status(st: dict) -> str:
    lines = []
    gpu = st.get("gpu") or {}
    lines.append(f"GPU {gpu.get('used_mib', '?')}/{gpu.get('total_mib', '?')} MiB, util {gpu.get('util', '?')}%, "
                 f"parker={st.get('parker')}{'  DRY RUN' if st.get('dry_run') else ''}")
    if st.get("mode"):
        m = st["mode"]
        lines.append(f"MODE: {m['name']} (idle window {m.get('idle_window_s', '?')} s, "
                     f"grace {m.get('grace_period_s', '?')} s)")
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
        elif h.get("snoozed_for_s"):
            note = f"snoozed, {h['snoozed_for_s']:.0f} s left"
        elif h.get("idle_s") is not None and h["state"] == "idle":
            note = f"no GPU work for {h['idle_s']:.0f} s"
        lines.append(f"{slot['slot']:<5} {h['name'] + ' (' + h['sid'] + ')':<14} {h['state']:<10} "
                     f"{h['mem_mib']:>8} {h['util']:>4}%  {note}")
    parked = [s for s in st["sessions"] if s["state"] == "parked"]
    if parked:
        lines.append("")
        lines.append("PARKED: " + ", ".join(
            f"{s['name']} ({s['sid']}, {s['parked_s']:.0f} s{', on disk' if s.get('on_disk') else ''}"
            + (f", back at {time.strftime('%H:%M', time.localtime(s['back_at']))}" if s.get("back_at") else "") + ")"
            for s in parked))
    lines.append("")
    if st["queue"]:
        lines.append("QUEUE:  " + "  ".join(f"{i + 1}. {q['user']}{' [returning]' if q['returning'] else ''} "
                                            f"({q['waiting_s']:.0f} s{_eta_text(q)})"
                                            for i, q in enumerate(st["queue"])))
    else:
        lines.append("QUEUE:  empty")
    s = st["stats"]
    lines.append(f"STATS:  reclaimed {s['reclaimed_gpu_s']:.0f} GPU-seconds, {s['parks']} parks, "
                 f"{s.get('restores', 0)} restores, {len(s['receipts'])} receipts")
    tips = [f"{x['name']}: {x['tip']['text']}" for x in st["sessions"] if x.get("tip")]
    if tips:
        lines.append("TIPS:   " + "\n        ".join(tips))
    return "\n".join(lines)


def _eta_text(q: dict) -> str:
    if q.get("eta_s") is None:
        return ""
    about = "~" if q.get("eta_basis") == "guess" else ""
    eta = q["eta_s"]
    return f", ETA {about}{eta:.0f} s" if eta < 90 else f", ETA {about}{eta / 60:.0f} min"


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


def _send_control(cfg: dict, msg: dict) -> dict | None:
    d = cfg["daemon"]
    try:
        reply = protocol.request(d["host"], d["port"], msg)
    except OSError as e:
        print(f"daemon not reachable: {e}", file=sys.stderr)
        return None
    if not reply or reply.get("type") == "error":
        print(f"error: {(reply or {}).get('error', 'no reply')}", file=sys.stderr)
        return None
    return reply


def cmd_control(cfg: dict, kind: str, session: str, **extra) -> int:
    reply = _send_control(cfg, {"type": kind, "session": session, **extra})
    if reply is None:
        return 1
    note = ""
    if kind == "snooze":
        note = f" (snoozed {reply['snooze_s']:.0f} s, {reply['snoozes_left']} left today)"
    elif kind == "away":
        note = f" (restored just before {time.strftime('%H:%M', time.localtime(reply['back_at']))})"
    print(f"ok: {reply['session']} is now {reply['state']}{note}")
    return 0


def parse_when(arg: str) -> dict:
    """'45' -> 45 minutes from now; '15:30' -> the next 15:30."""
    if ":" in arg:
        hh, mm = (int(x) for x in arg.split(":", 1))
        t = time.localtime()
        ts = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hh, mm, 0, 0, 0, -1))
        return {"until": ts if ts > time.time() else ts + 86400}
    return {"minutes": float(arg)}


def cmd_why(cfg: dict, args) -> int:
    evs = read_session_events(resolve_path(cfg["daemon"]["db_path"]), args.session)
    if not evs:
        print(f"no events for session {args.session} (use the session id, e.g. s3, from `gpuq status`)",
              file=sys.stderr)
        return 1
    print(f"Timeline for {args.session} ({evs[0]['user']}):")
    for ev in timeline(evs):
        if ev["kind"] == "notify":
            continue
        print(f"  {time.strftime('%H:%M:%S', time.localtime(ev['ts']))}  {ev['text']}")
    return 0


def cmd_stats(cfg: dict, args) -> int:
    lb = leaderboard(resolve_path(cfg["daemon"]["db_path"]), args.days)
    users = [u for u in lb["users"] if not args.user or u["user"] == args.user]
    if not users:
        print("no sessions recorded yet")
        return 0
    print(f"Last {args.days:g} days.  {'USER':<12} {'GPU-h':>7} {'GAVE BACK h':>11} {'HELPED':>6} "
          f"{'SAVED OTHERS h':>14} {'AVG WAIT':>8}")
    for i, u in enumerate(users, 1):
        wait = "-" if u["avg_wait_s"] is None else f"{u['avg_wait_s']:.0f} s"
        print(f"{i:>15}. {u['user']:<12} {u['gpu_h']:>7.2f} {u['given_back_h']:>11.2f} {u['people_helped']:>6} "
              f"{u['wait_saved_for_others_h']:>14.2f} {wait:>8}")
    t = lb["totals"]
    print(f"Lab: {t['gpu_h']:.2f} GPU-h used, {t['given_back_h']:.2f} h given back by parking, "
          f"{t['people_helped']} slots handed on")
    return 0


def cmd_mode(cfg: dict, args) -> int:
    if args.name == "off":
        msg = {"type": "clear_mode"}
    else:
        msg = {"type": "set_mode", "name": args.name, "idle_window_s": args.idle, "grace_period_s": args.grace,
               "minutes": args.minutes}
    reply = _send_control(cfg, msg)
    if reply is None:
        return 1
    m = reply.get("mode")
    print("mode: normal" if not m else f"mode: {m['name']} (idle window {m.get('idle_window_s')} s, "
                                       f"grace {m.get('grace_period_s')} s)")
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
    for name, helptext in (("resume", "restore a parked session"), ("park-now", "park a session immediately"),
                           ("snooze", "postpone the idle warning")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("session", help="session id (s3) or user/name")
    a = sub.add_parser("away", help="park now, restore just before you're back")
    a.add_argument("session")
    a.add_argument("when", help="minutes (45) or a time today (15:30)")
    w = sub.add_parser("why", help="timeline: why was this session parked?")
    w.add_argument("session", help="session id, e.g. s3")
    st = sub.add_parser("stats", help="impact stats and leaderboard")
    st.add_argument("--user", default=None)
    st.add_argument("--days", type=float, default=7)
    m = sub.add_parser("mode", help="deadline mode: stricter idle window (or `mode off`)")
    m.add_argument("name", help="a name for the mode, or `off`")
    m.add_argument("--idle", type=float, default=None, help="idle window, seconds")
    m.add_argument("--grace", type=float, default=None, help="grace period, seconds")
    m.add_argument("--minutes", type=float, default=None, help="switch back after this long")
    dr = sub.add_parser("dryrun-report", help="what a dry run would have reclaimed")
    dr.add_argument("--days", type=float, default=None)
    args = ap.parse_args(argv)
    cfg = load_config(overrides={"daemon.port": args.port} if args.port else None)
    if args.cmd == "status":
        return cmd_status(cfg, args)
    if args.cmd == "request":
        return cmd_request(cfg, args)
    if args.cmd == "why":
        return cmd_why(cfg, args)
    if args.cmd == "stats":
        return cmd_stats(cfg, args)
    if args.cmd == "mode":
        return cmd_mode(cfg, args)
    if args.cmd == "dryrun-report":
        since = time.time() - args.days * 86400 if args.days else 0.0
        print(dryrun_report(resolve_path(cfg["daemon"]["db_path"]), since)["text"])
        return 0
    if args.cmd == "away":
        return cmd_control(cfg, "away", args.session, **parse_when(args.when))
    kind = {"park-now": "park_now", "resume": "resume", "snooze": "snooze"}[args.cmd]
    return cmd_control(cfg, kind, args.session)


if __name__ == "__main__":
    sys.exit(main())
