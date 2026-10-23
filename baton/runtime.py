"""Track the processes this project starts, so they (and only they) can be cleaned up later.

Every process we launch is appended to data/run/pids.txt as "pid create_time label". Cleanup kills a
recorded PID only if a process with that PID still exists AND has the same creation time, so a PID
that Windows has reused for someone else's program is never touched.
"""
from __future__ import annotations

import os
from pathlib import Path

import psutil

from .config import resolve_path

PID_FILE = resolve_path("data/run/pids.txt")


def record_pid(pid: int, label: str, pid_file: Path = PID_FILE) -> None:
    """Remember a process we started."""
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        created = psutil.Process(pid).create_time()
    except psutil.NoSuchProcess:
        return
    with open(pid_file, "a", encoding="utf-8") as f:
        f.write(f"{pid} {created:.3f} {label}\n")


def recorded(pid_file: Path = PID_FILE) -> list[tuple[int, float, str]]:
    if not pid_file.exists():
        return []
    out = []
    for line in pid_file.read_text(encoding="utf-8").splitlines():
        parts = line.split(" ", 2)
        if len(parts) >= 2:
            out.append((int(parts[0]), float(parts[1]), parts[2] if len(parts) > 2 else ""))
    return out


def alive_recorded(pid_file: Path = PID_FILE) -> list[psutil.Process]:
    """Recorded processes that are still running (same PID and same creation time)."""
    procs = []
    for pid, created, _ in recorded(pid_file):
        if pid == os.getpid():
            continue
        try:
            p = psutil.Process(pid)
            if abs(p.create_time() - created) < 0.01:
                procs.append(p)
        except psutil.NoSuchProcess:
            pass
    return procs


def cleanup(pid_file: Path = PID_FILE, timeout: float = 5.0) -> int:
    """Terminate every still-running recorded process, then clear the file. Returns how many were stopped."""
    procs = alive_recorded(pid_file)
    for p in procs:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
    _, still = psutil.wait_procs(procs, timeout=timeout)
    for p in still:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    if pid_file.exists():
        pid_file.unlink()
    return len(procs)


if __name__ == "__main__":
    print(f"stopped {cleanup()} leftover process(es)")
