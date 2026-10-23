"""The control channel: localhost TCP, one JSON object per line, UTF-8.

Client (student process) -> daemon:
    register   {user, pid, slot_mem_gib, name, whitelist}     first message on a connection
    report     {mem_reserved, mem_allocated, steps, last_step_ago_s, in_step, state}   every ~1 s
    request    {}                       a parked owner wants their GPU back (sent when they call step())
    parked     {park_s, bytes, reserved_after}               ack: park finished
    resumed    {restore_s, slot}                              ack: restore finished
    park_failed / resume_failed {error}
    bye        {}

Daemon -> client:
    registered {session, state: running|queued, slot, cap_fraction}
    grant      {slot, cap_fraction}     a queued session got a slot
    warn       {park_in_s, idle_s}      you look idle; you will be parked unless GPU work starts
    park       {}                        park at the next safe point
    resume     {slot, cap_fraction}      restore onto this slot

Control clients (CLI, dashboard) may send `status`, `park_now {session}`, `resume {session}` and get
one reply line back. Lines are small, so a plain blocking socket with makefile() is enough.
"""
from __future__ import annotations

import json
import socket
from typing import Any, TextIO


def encode(msg: dict[str, Any]) -> str:
    return json.dumps(msg, separators=(",", ":")) + "\n"


def send(f: TextIO, msg: dict[str, Any]) -> None:
    f.write(encode(msg))
    f.flush()


def recv(f: TextIO) -> dict[str, Any] | None:
    """Read one message; None on EOF."""
    line = f.readline()
    if not line:
        return None
    return json.loads(line)


def connect(host: str, port: int, timeout: float | None = 5.0) -> tuple[socket.socket, TextIO]:
    s = socket.create_connection((host, port), timeout=timeout)
    s.settimeout(None)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return s, s.makefile("rw", encoding="utf-8", newline="\n")


def request(host: str, port: int, msg: dict[str, Any], timeout: float = 5.0) -> dict[str, Any] | None:
    """One-shot request/reply for control clients."""
    s, f = connect(host, port, timeout)
    try:
        s.settimeout(timeout)
        send(f, msg)
        return recv(f)
    finally:
        s.close()
