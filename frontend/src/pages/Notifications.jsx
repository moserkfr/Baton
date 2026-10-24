import { useState } from "react";
import { api } from "../api";
import useFetch from "../components/useFetch";
import PageHeader from "../components/PageHeader";
import { useSheet } from "../components/sheet";
import { dateTime, short } from "../format";
import "./Notifications.css";

const WHAT = ["", "warn", "parked", "ready", "restored", "next", "spilled"];

// The in-app inbox: every `notify` event, optionally for one user.
export default function Notifications({ myUser }) {
  const { openSession } = useSheet();
  const [user, setUser] = useState(myUser || "");
  const [what, setWhat] = useState("");
  const { data, error } = useFetch(() => api.notifications(user), [user], 3000);
  const [perm, setPerm] = useState("Notification" in window ? Notification.permission : "unsupported");
  const list = (data || []).filter((n) => !what || n.detail?.what === what);

  return (
    <div className="page inbox-page">
      <PageHeader eyebrow="Inbox" title="Every heads-up," soft="before it happens.">
        Warnings before a park, a ping when your GPU is ready, and a note when you are next in the queue.
        Webhooks to a phone, Discord or Slack are set under <code>notify.webhooks</code> in config.yaml.
      </PageHeader>

      <div className="toolbar" style={{ marginBottom: 16 }}>
        <label className="field">User<input value={user} onChange={(e) => setUser(e.target.value)} placeholder="everyone" /></label>
        <div className="chips">
          {WHAT.map((w) => (
            <button key={w} className={what === w ? "chip on" : "chip"} onClick={() => setWhat(w)}>{w || "all"}</button>
          ))}
        </div>
        <div className="perm">
          <span className="muted">Browser notifications: {perm}</span>
          {perm === "default" && (
            <button className="btn sm" onClick={() => Notification.requestPermission().then(setPerm)}>Enable</button>
          )}
        </div>
      </div>

      {error && <p className="error">{error}</p>}
      <section className="panel">
        {list.length === 0 ? <p className="empty">No notifications.</p> : (
          <ul className="notes">
            {list.map((n) => (
              <li key={n.id} className={n.detail?.what} onClick={() => n.sid && openSession(n.sid)}>
                <span className="note-kind mono">{n.detail?.what}</span>
                <div>
                  <b>{n.detail?.title}</b>
                  <div className="muted">{n.detail?.body}</div>
                </div>
                <div className="note-meta">
                  <span>{short(n.user)}</span>
                  <span className="mono muted">{dateTime(n.ts)}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
