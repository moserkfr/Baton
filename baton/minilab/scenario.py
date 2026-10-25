"""The scripted live demo: 2 virtual slots, 4 students, the real daemon, the real GPU.

    python -m baton.minilab.scenario            # writes results/scenario_runN.{log,json}
    python -m baton.minilab.scenario --pace 3   # slower, with pauses, for presenting live

Story (idle window 30 s, grace 10 s):
  1. A and B start and take both slots. Both train.
  2. B stops working. C asks for a GPU and is queued.
  3. B is idle -> warned -> parked (GPU memory to host RAM). C gets B's slot immediately.
  4. D (a heavy job) asks for a GPU and is queued.
  5. B comes back: queued with returning-owner priority. When C finishes, B (not D, though D waited
     longer) gets the slot, is restored, and its checksum matches the one from before the park.
  6. A finishes; D gets that slot. Everyone exits; VRAM returns to baseline.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .. import protocol
from ..config import load_config, resolve_path
from ..daemon.state import clear
from .launcher import MiniLab, port_is_free, wait_for_port


class Recorder:
    def __init__(self, path: Path):
        self.t0 = time.time()
        self.f = open(path, "w", encoding="utf-8")
        self.marks: dict[str, float] = {}

    def __call__(self, source: str, text: str) -> None:
        line = f"[+{time.time() - self.t0:7.2f}s] {source:>8}: {text}"
        print(line, flush=True)
        self.f.write(line + "\n")
        self.f.flush()

    def step(self, text: str) -> None:
        self("SCENARIO", f"=== {text}")

    def mark(self, name: str) -> float:
        self.marks[name] = time.time() - self.t0
        return self.marks[name]


def gpu_used_mib() -> float:
    import pynvml

    pynvml.nvmlInit()
    used = pynvml.nvmlDeviceGetMemoryInfo(pynvml.nvmlDeviceGetHandleByIndex(0)).used / 2**20
    pynvml.nvmlShutdown()
    return used


def status(port: int) -> dict:
    return protocol.request("127.0.0.1", port, {"type": "status"})["status"]


def session_state(port: int, user: str) -> str | None:
    for s in status(port)["sessions"]:
        if s["name"] == user:
            return s["state"]
    return None


def wait_state(port: int, user: str, state: str, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if session_state(port, user) == state:
            return
        time.sleep(0.2)
    raise TimeoutError(f"{user} never reached {state!r}")


def next_run_number(out: Path) -> int:
    n = 1
    while (out / f"scenario_run{n}.json").exists():
        n += 1
    return n


def run(idle_s: float, grace_s: float, pace: float, port: int, delay: float = 0.0) -> dict:
    for left in range(int(delay), 0, -1):   # time to switch to the browser and start a screen recording
        print(f"\rstarting in {left:2d} s ", end="", flush=True)
        time.sleep(1)
    out = resolve_path("results")
    n = next_run_number(out)
    rec = Recorder(out / f"scenario_run{n}.log")
    clear(resolve_path("data/state.db"))   # fresh event log; works while the dashboard has it open
    summary: dict = {"run": n, "idle_window_s": idle_s, "grace_s": grace_s, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    summary["vram_baseline_mib"] = gpu_used_mib()
    rec("SCENARIO", f"VRAM before anything: {summary['vram_baseline_mib']:.0f} MiB")
    sargs = ["--port", str(port)]

    with MiniLab(rec) as lab:
        rec.step("start the daemon (2 slots x 2.2 GiB)")
        lab.spawn("daemon", ["-m", "baton.daemon.server", "--set", f"daemon.port={port}",
                             "--set", f"policy.idle_window_s={idle_s}", "--set", f"policy.grace_period_s={grace_s}",
                             "--set", "daemon.dry_run=false"])
        wait_for_port("127.0.0.1", port)

        rec.step("1. students A and B log in and take both slots")
        a = lab.spawn("A", ["-m", "baton.minilab.students", "A", *sargs])
        b = lab.spawn("B", ["-m", "baton.minilab.students", "B", *sargs])
        a.wait_event("ready", 90)
        b.wait_event("ready", 90)
        a.send("work 600 0.1")             # A trains for the whole demo (interrupted at the end)
        b.send("work 12")
        b_work1 = b.wait_event("worked", 40)
        b.send("checksum")
        ck_before = b.wait_event("checksum", 10)["checksum"]
        summary["b_checksum_before"] = ck_before
        summary["b_loss_before_park"] = b_work1["last_loss"]
        summary["vram_both_running_mib"] = gpu_used_mib()
        st = status(port)
        summary["b_mem_reserved_mib"] = next(s["mem_mib"] for s in st["sessions"] if s["name"] == "B")
        rec("SCENARIO", f"both slots busy; VRAM {summary['vram_both_running_mib']:.0f} MiB; "
                        f"B holds {summary['b_mem_reserved_mib']} MiB; B checksum {ck_before}")

        rec.step("2. B stops working (goes to lunch). C asks for a GPU")
        t_b_idle = rec.mark("b_idle")
        time.sleep(5 * pace)
        c = lab.spawn("C", ["-m", "baton.minilab.students", "C", *sargs])
        c.wait_event("connecting", 60)
        wait_state(port, "C", "queued", 30)
        t_c_queued = rec.mark("c_queued")
        rec("SCENARIO", "C is queued: no free slot")

        rec.step(f"3. B idle for {idle_s:.0f} s -> warned; after {grace_s:.0f} s grace -> parked")
        b.wait_event("daemon_warn", idle_s + 20)
        summary["b_warned_after_idle_s"] = rec.mark("b_warned") - t_b_idle
        b.wait_event("daemon_park", grace_s + 10)
        wait_state(port, "B", "parked", 30)
        rec.mark("b_parked")
        time.sleep(0.5)
        summary["vram_after_park_mib"] = gpu_used_mib()
        c_granted = c.wait_event("granted", 30)
        summary["c_waited_s"] = c_granted["waited_s"]
        b.send("checksum")
        ck_parked = b.wait_event("checksum", 10)
        summary["b_checksum_while_parked"] = ck_parked["checksum"]
        summary["b_devices_while_parked"] = ck_parked["devices"]
        rec("SCENARIO", f"B parked; VRAM {summary['vram_both_running_mib']:.0f} -> {summary['vram_after_park_mib']:.0f} MiB; "
                        f"C got the slot after waiting {summary['c_waited_s']:.1f} s; "
                        f"B checksum while parked {ck_parked['checksum']} on {ck_parked['devices']}")
        c.wait_event("ready", 60)
        c.send("work 20 0.05")

        rec.step("4. D (heavy job) asks for a GPU and is queued")
        time.sleep(2 * pace)
        d = lab.spawn("D", ["-m", "baton.minilab.students", "D", *sargs])
        d.wait_event("connecting", 60)
        wait_state(port, "D", "queued", 30)
        rec.mark("d_queued")

        rec.step("5. B comes back and runs a cell; queued with returning-owner priority")
        time.sleep(2 * pace)
        t_b_back = rec.mark("b_back")
        b.send("touch")
        time.sleep(1.0)
        q = status(port)["queue"]
        summary["queue_when_b_returns"] = [(e["user"], e["returning"]) for e in q]
        rec("SCENARIO", f"queue now: {summary['queue_when_b_returns']}")
        c.wait_event("worked", 40)
        c.send("exit")
        touched = b.wait_event("touched", 40)
        summary["b_return_wait_s"] = touched["waited_s"]
        summary["b_checksum_after_restore"] = touched["checksum"]
        summary["checksum_match"] = touched["checksum"] == ck_before == ck_parked["checksum"]
        rec("SCENARIO", f"B restored after waiting {touched['waited_s']:.2f} s for C to finish; "
                        f"checksum {touched['checksum']} (match: {summary['checksum_match']})")
        b.send("work 8")
        b_work2 = b.wait_event("worked", 30)
        summary["b_loss_after_restore"] = b_work2["first_loss"]
        summary["b_loss_end"] = b_work2["last_loss"]
        summary["d_state_while_b_restored"] = session_state(port, "D")

        rec.step("6. A finishes; D gets the slot")
        a.send("exit")
        d_granted = d.wait_event("granted", 30)
        summary["d_waited_s"] = d_granted["waited_s"]
        d.wait_event("ready", 60)
        d.send("work 6 0.05")
        d.wait_event("worked", 30)

        st = status(port)
        summary["stats"] = st["stats"]
        events = st["events"]
        parked_ev = next(e for e in events if e["kind"] == "parked" and e["user"] == "student-B")
        restored_ev = next(e for e in events if e["kind"] == "restored" and e["user"] == "student-B")
        summary["b_park_s"] = parked_ev["detail"]["park_s"]
        summary["b_parked_gib"] = parked_ev["detail"]["gib"]
        summary["b_restore_s"] = restored_ev["detail"]["restore_s"]
        order = [e["user"] for e in events if e["kind"] in ("restoring", "grant") and e["ts"] - rec.t0 > t_b_back]
        summary["grant_order_after_b_returns"] = order
        summary["returning_priority_worked"] = bool(order) and order[0] == "student-B"
        rec.step("everyone logs out")
        for ch in (b, d):
            ch.send("exit")
        time.sleep(2)

    time.sleep(2)
    summary["vram_end_mib"] = gpu_used_mib()
    summary["vram_back_to_baseline"] = summary["vram_end_mib"] - summary["vram_baseline_mib"] < 64
    summary["duration_s"] = time.time() - rec.t0
    rec("SCENARIO", f"VRAM at the end: {summary['vram_end_mib']:.0f} MiB (baseline {summary['vram_baseline_mib']:.0f})")
    rec("SCENARIO", "SUMMARY " + json.dumps(summary))
    (out / f"scenario_run{n}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    rec.f.close()
    return summary


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="scripted mini-lab demo")
    ap.add_argument("--idle", type=float, default=30.0)
    ap.add_argument("--grace", type=float, default=10.0)
    ap.add_argument("--pace", type=float, default=1.0, help="multiply the pauses (slower live demo)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--delay", type=float, default=0.0, help="wait this many seconds before starting")
    args = ap.parse_args(argv)
    port = args.port or load_config()["daemon"]["port"]
    if not port_is_free("127.0.0.1", port):
        port += 1
    run(args.idle, args.grace, args.pace, port, args.delay)


if __name__ == "__main__":
    main()
