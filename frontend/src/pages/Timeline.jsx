import { useState } from "react";
import { api } from "../api";
import useFetch from "../components/useFetch";
import PageHeader from "../components/PageHeader";
import TimelineList from "../components/TimelineList";
import { useSheet } from "../components/sheet";
import { useStatus } from "../status";
import { go, href } from "../router";
import { short } from "../format";
import "./Timeline.css";

// "Why was I parked?": every event for one session, as plain-English sentences.
export default function Timeline({ sid }) {
  const { status } = useStatus();
  const { openSession } = useSheet();
  const [input, setInput] = useState(sid || "");
  const { data, error } = useFetch(() => (sid ? api.timeline(sid) : Promise.resolve(null)), [sid], 3000);
  const live = status?.sessions || [];

  return (
    <div className="page why">
      <PageHeader eyebrow="Explain" title="Why was I parked?" soft="Every step, in plain English.">
        Pick a session to see when it went idle, when it was warned, where its memory went, and when it came back.
      </PageHeader>

      <div className="why-grid">
        <aside className="panel soft">
          <form onSubmit={(e) => { e.preventDefault(); if (input) go("why", input); }} className="toolbar">
            <label className="field">Session id<input value={input} onChange={(e) => setInput(e.target.value)} placeholder="s3" /></label>
            <button className="btn" type="submit">Show</button>
          </form>
          <div className="eyebrow" style={{ margin: "22px 0 8px" }}>Live sessions</div>
          <ul className="why-list">
            {live.map((s) => (
              <li key={s.sid}>
                <a href={href("why", s.sid)} className={s.sid === sid ? "on" : ""}>
                  <span className="mono">{s.sid}</span> {short(s.name)} <span className="muted">· {s.state}</span>
                </a>
              </li>
            ))}
          </ul>
        </aside>

        <section className="panel">
          {!sid && <p className="empty">Choose a session on the left.</p>}
          {error && <p className="error">{error}</p>}
          {data && (
            <>
              <div className="panel-title">
                <h2>Session {data.sid} · {short(data.user)}</h2>
                <button className="btn ghost sm" onClick={() => openSession(data.sid)}>Open controls ↑</button>
              </div>
              <TimelineList timeline={data.timeline} />
            </>
          )}
        </section>
      </div>
    </div>
  );
}
