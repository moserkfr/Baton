"""Notifications: "your GPU is ready", "parking in 5 min", "you're next".

Three places a notification goes:
1. the student's own process (a `notify` message; the helper library prints it, a notebook shows it);
2. the SQLite inbox (event kind `notify`), which the dashboard serves and streams;
3. optional webhooks from config.yaml: Discord, Slack, ntfy (phone push with no account), or plain JSON.

Webhooks are sent from a background thread so a slow server can never stall the daemon, and failures are
only logged. With no webhooks configured, nothing leaves the machine.
"""
from __future__ import annotations

import json
import queue
import threading
import urllib.request
from typing import Any, Callable

Sender = Callable[[str, bytes, dict[str, str]], None]


def _http_post(url: str, body: bytes, headers: dict[str, str]) -> None:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        resp.read()


def webhook_payload(kind: str, user: str, title: str, body: str) -> tuple[bytes, dict[str, str]]:
    """Request body and headers for one webhook kind."""
    if kind == "discord":
        return json.dumps({"content": f"**{title}** ({user})\n{body}"}).encode(), {"Content-Type": "application/json"}
    if kind == "slack":
        return json.dumps({"text": f"*{title}* ({user})\n{body}"}).encode(), {"Content-Type": "application/json"}
    if kind == "ntfy":
        # HTTP headers must be latin-1; keep the title plain
        safe = title.encode("latin-1", "replace").decode("latin-1")
        return body.encode(), {"Title": f"{safe} ({user})", "Tags": "computer"}
    return (json.dumps({"user": user, "title": title, "body": body}).encode(),
            {"Content-Type": "application/json"})


class Notifier:
    def __init__(self, cfg: dict[str, Any] | None, sender: Sender | None = None,
                 log: Callable[[str], None] | None = None):
        cfg = cfg or {}
        self.kinds = set(cfg.get("kinds") or [])
        self.webhooks = list(cfg.get("webhooks") or [])
        self.sender = sender or _http_post
        self.log = log or (lambda text: None)
        self.sent: list[dict[str, Any]] = []         # last few, for tests and the status page
        self._q: queue.Queue = queue.Queue()
        if self.webhooks:
            threading.Thread(target=self._worker, name="notify", daemon=True).start()

    def wants(self, kind: str) -> bool:
        return kind in self.kinds

    def push(self, user: str, kind: str, title: str, body: str) -> None:
        """Queue webhook deliveries for this notification (returns at once)."""
        for hook in self.webhooks:
            users = hook.get("users") or []
            if users and user not in users:
                continue
            self._q.put((hook, user, kind, title, body))

    def flush(self, timeout: float = 5.0) -> None:
        """Wait until queued webhooks were attempted (tests, shutdown)."""
        done = threading.Event()
        self._q.put(done)
        done.wait(timeout)

    def _worker(self) -> None:
        while True:
            item = self._q.get()
            if isinstance(item, threading.Event):
                item.set()
                continue
            hook, user, kind, title, body = item
            data, headers = webhook_payload(hook.get("kind", "generic"), user, title, body)
            try:
                self.sender(hook["url"], data, headers)
                self.sent.append({"url": hook["url"], "user": user, "kind": kind})
                del self.sent[:-20]
            except Exception as e:   # never let a webhook break the daemon
                self.log(f"webhook {hook.get('kind')} failed: {e!r}")
