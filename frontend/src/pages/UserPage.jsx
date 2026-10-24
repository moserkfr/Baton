import { useState } from "react";
import { api } from "../api";
import useFetch from "../components/useFetch";
import PageHeader from "../components/PageHeader";
import Receipts from "../components/Receipts";
import StatePill from "../components/StatePill";
import StatsGrid from "../components/StatsGrid";
import { useSheet } from "../components/sheet";
import { useStatus } from "../status";
import { go } from "../router";
import { clock, dateTime, dur, short } from "../format";
import "./UserPage.css";

// "My page": live sessions, impact stats, notifications, receipts and past sessions for one user.
export default function UserPage({ user, myUser, setMyUser }) {
  const { status } = useStatus();
  const { openSession } = useSheet();
  const [input, setInput] = useState(user || myUser || "");
  const [days, setDays] = useState(7);
  const span = Math.max(1, Number(days) || 1);   // a cleared or zero field still means "at least a day"
  const { data, error } = useFetch(() => (user ? api.user(user, span) : Promise.resolve(null)), [user, span], 3000);
  const users = [...new Set((status?.sessions || []).map((s) => s.user))];

  return (
    <div className="page user-page">
      <PageHeader eyebrow="My page" title={user ? `Hi, ${short(user)}.` : "Whose page?"} soft={user ? "Here is your GPU, in one place." : ""}>
        Your sessions, the GPU time you gave back, and every notification Baton sent you.
      </PageHeader>

      <form className="toolbar user-pick" onSubmit={(e) => { e.preventDefault(); if (input) go("user", input); }}>
        <label className="field">User
          <input list="known-users" value={input} onChange={(e) => setInput(e.target.value)} placeholder="student-B" />
        </label>
        <datalist id="known-users">{users.map((u) => <option key={u} value={u} />)}</datalist>
        <label className="field">Days
          <input type="number" min="1" value={days} onChange={(e) => setDays(e.target.value)} style={{ width: 80 }} />
        </label>
        <button className="btn" type="submit">Open <span className="arrow">→</span></button>
        {user && (myUser === user
          ? <button type="button" className="btn ghost" onClick={() => setMyUser("")}>This isn't me</button>
          : <button type="button" className="btn ghost" onClick={() => setMyUser(user)}>This is me: send me notifications</button>)}
      </form>

      {error && <p className="error">{error}</p>}
      {data && (
        <div className="stack">
          <section>
            <div className="eyebrow section-label">Live sessions</div>
            {data.live_sessions.length === 0 ? <p className="empty">No live sessions.</p> : (
              <div className="session-cards">
                {data.live_sessions.map((s) => (
                  <article key={s.sid} className={`session-card ${s.state}`} onClick={() => openSession(s.sid)}>
                    <div className="row"><span className="mono muted">{s.sid}{s.slot != null && ` · slot ${s.slot}`}</span><StatePill state={s.state} /></div>
                    <h3>{short(s.name)}</h3>
                    <div className="row muted">
                      <span>{s.mem_mib} MiB · {s.util}%</span>
                      <span>{s.state === "parked" ? `parked ${dur(s.parked_s)}` : s.idle_s != null ? `idle ${dur(s.idle_s)}` : ""}</span>
                    </div>
                    {s.tip && <p className="tip">{s.tip.text}</p>}
                    <span className="eyebrow">Open controls ↑</span>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section>
            <div className="eyebrow section-label">Impact · last {data.days} days</div>
            {!data.stats ? <p className="empty">No finished sessions in this window.</p> : <StatsGrid s={data.stats} />}
          </section>

          <div className="grid-2">
            <section className="panel">
              <div className="panel-title"><h2>Notifications</h2></div>
              {data.notifications.length === 0 ? <p className="empty">Nothing yet.</p> : (
                <ul className="inbox">
                  {data.notifications.map((n) => (
                    <li key={n.id} onClick={() => n.sid && openSession(n.sid)}>
                      <span className="pill accent"><i />{n.detail?.what}</span>
                      <div><b>{n.detail?.title}</b><div className="muted">{n.detail?.body}</div></div>
                      <span className="mono muted">{clock(n.ts)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section className="panel soft">
              <div className="panel-title"><h2>Receipts</h2></div>
              <Receipts receipts={data.receipts} />
            </section>
          </div>

          <section className="panel">
            <div className="panel-title"><h2>Past sessions</h2></div>
            {data.past_sessions.length === 0 ? <p className="empty">None in this window.</p> : (
              <div className="tablewrap">
                <table>
                  <thead><tr>
                    <th>Session</th><th>Started</th><th>Ended</th><th className="num">GPU time</th><th className="num">Parked</th>
                    <th className="num">Parks</th><th className="num">Peak</th><th className="num">Snoozes</th>
                  </tr></thead>
                  <tbody>{data.past_sessions.map((s, i) => (
                    <tr key={`${s.sid}-${i}`} className="clickable" onClick={() => openSession(s.sid)}>
                      <td className="mono">{s.sid}</td>
                      <td>{dateTime(s.registered_at)}</td>
                      <td>{dateTime(s.ended_at)}</td>
                      <td className="num">{dur(s.gpu_seconds)}</td>
                      <td className="num">{dur(s.parked_seconds)}</td>
                      <td className="num">{s.parks} / {s.restores}</td>
                      <td className="num">{s.peak_allocated != null ? `${(s.peak_allocated / 2 ** 30).toFixed(2)} GiB` : "–"}</td>
                      <td className="num">{s.snoozes}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
