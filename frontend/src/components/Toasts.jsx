import { useEffect, useState } from "react";
import { useStatus } from "../status";
import { useSheet } from "./sheet";
import { short } from "../format";
import "./Toasts.css";

// Pops up `notify` events from the live stream. If "my user" is set, only that user's notifications
// show, and they also go to the browser's Notification API when permission is granted.
export default function Toasts({ myUser }) {
  const { onEvent } = useStatus();
  const { openSession } = useSheet();
  const [toasts, setToasts] = useState([]);

  useEffect(() => onEvent((ev) => {
    if (ev.kind !== "notify") return;
    if (myUser && ev.user !== myUser) return;
    const d = ev.detail || {};
    setToasts((t) => [...t, { id: ev.id, sid: ev.sid, user: ev.user, what: d.what, title: d.title, body: d.body }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== ev.id)), 8000);
    if (myUser && "Notification" in window && Notification.permission === "granted") {
      try {
        new Notification(d.title || "Baton", { body: d.body });
      } catch { /* mobile Chrome only allows notifications from a service worker */ }
    }
  }), [myUser, onEvent]);

  const dismiss = (id) => setToasts((x) => x.filter((y) => y.id !== id));
  return (
    <div className="toasts">
      {toasts.map((t) => (
        <div key={t.id} className="toast" onClick={() => { dismiss(t.id); if (t.sid) openSession(t.sid); }}>
          <div className="eyebrow"><span className="dot" />{t.what} · {short(t.user)}</div>
          <b>{t.title}</b>
          <div className="muted">{t.body}</div>
        </div>
      ))}
    </div>
  );
}
