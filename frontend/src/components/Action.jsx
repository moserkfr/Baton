import { useState } from "react";
import { useStatus } from "../status";

// A button that runs an API call and shows the refusal reason (400) or "daemon not running" (503).
export default function Action({ run, children, title, ghost = true, small = true }) {
  const { refresh } = useStatus();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  async function click(e) {
    e.stopPropagation();
    setBusy(true); setMsg(null);
    try {
      const reply = await run();
      if (reply?.snoozes_left != null) setMsg({ ok: true, text: `${reply.snoozes_left} snoozes left` });
      else if (reply?.back_at) setMsg({ ok: true, text: `back at ${new Date(reply.back_at * 1000).toLocaleTimeString()}` });
      else setMsg({ ok: true, text: "done" });
    } catch (err) {
      setMsg({ ok: false, text: err.message });
    } finally {
      setBusy(false);
      refresh();
    }
  }

  return (
    <span className="action">
      <button className={`btn ${ghost ? "ghost" : ""} ${small ? "sm" : ""}`} onClick={click} disabled={busy} title={title}>
        {busy ? "Working…" : children}
      </button>
      {msg && <small className={msg.ok ? "ok" : "error"}>{msg.text}</small>}
    </span>
  );
}
