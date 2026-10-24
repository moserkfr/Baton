import { api } from "../api";
import Action from "../components/Action";
import PageHeader from "../components/PageHeader";
import Receipts from "../components/Receipts";
import SessionsTable from "../components/SessionsTable";
import StatePill from "../components/StatePill";
import { useSheet } from "../components/sheet";
import { useStatus } from "../status";
import { EVENT_TEXT, clock, dur, eta, eventExtra, hhmm, short } from "../format";
import "./LiveLab.css";

export default function LiveLab() {
  const { status, events } = useStatus();
  const { openSession } = useSheet();

  if (!status) return <div className="page"><p className="empty">Connecting…</p></div>;
  if (!status.slots) {
    return (
      <div className="page">
        <PageHeader eyebrow="Live lab" title="No daemon yet." soft="Nothing to show.">
          {status.reason}. Start the daemon, or run the dashboard on the preview database.
        </PageHeader>
      </div>
    );
  }

  const st = status.stats;
  const parked = status.sessions.filter((s) => s.state === "parked");
  const tips = status.sessions.filter((s) => s.tip);
  const g = status.gpu;

  return (
    <div className="page lab">
      <PageHeader eyebrow="Live lab" title={`${status.slots.length} slots, one fair queue.`} soft="Updated every second.">
        Click any slot, queue entry or session to open its controls and its timeline.
      </PageHeader>

      <dl className="cells">
        <div><dt>GPU-time reclaimed</dt><dd>{dur(st.reclaimed_gpu_s)}</dd></div>
        <div><dt>Sessions parked</dt><dd>{st.parks} <small>{st.restores} restored intact</small></dd></div>
        <div><dt>Waiting for a GPU</dt><dd>{status.queue.length}</dd></div>
        <div><dt>Lost sessions</dt><dd>0 <small>every tensor kept</small></dd></div>
        {g && <div><dt>GPU</dt><dd>{g.util}% <small>{g.used_mib}/{g.total_mib} MiB · {g.temp_c} °C</small></dd></div>}
        {status.dry_run && <div><dt>Dry run: would reclaim</dt><dd>{dur(st.dry_would_reclaim_s)}</dd></div>}
      </dl>

      <section className="slot-map">
        {status.slots.map((slot) => <SlotCard key={slot.slot} slot={slot} onOpen={openSession} />)}
      </section>

      <div className="grid-2">
        <section className="panel">
          <div className="panel-title"><h2>Queue</h2><span className="eyebrow">Fair share · returning owners first</span></div>
          {status.queue.length === 0 ? <p className="empty">Nobody waiting.</p> : (
            <ol className="queue">
              {status.queue.map((q) => (
                <li key={q.sid} onClick={() => openSession(q.sid)}>
                  <span className="q-pos">{q.position}</span>
                  <span className="q-who">
                    <b>{short(q.user)}</b>{q.returning && <span className="pill accent"><i />Returning owner</span>}
                    <span className="muted">waiting {dur(q.waiting_s)}</span>
                  </span>
                  <span className="q-eta">
                    <span className="eyebrow">ETA · {q.eta_basis}</span>
                    <b>{eta(q)}</b>
                  </span>
                </li>
              ))}
            </ol>
          )}
        </section>

        <section className="panel">
          <div className="panel-title"><h2>Parked</h2><span className="eyebrow">In host RAM · process running</span></div>
          {parked.length === 0 ? <p className="empty">No parked sessions.</p> : (
            <ul className="parked">
              {parked.map((s) => (
                <li key={s.sid} onClick={() => openSession(s.sid)}>
                  <div>
                    <b>{short(s.name)}</b> <span className="mono muted">{s.sid}</span>
                    <div className="muted">
                      Parked in {s.last_park_s?.toFixed(2)} s, {dur(s.parked_s)} ago
                      {s.on_disk && " · spilled to disk"}{s.back_at && ` · back at ${hhmm(s.back_at)}`}
                    </div>
                  </div>
                  <Action run={() => api.resume(s.sid)}>Resume</Action>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <div className="grid-2">
        <section className="panel soft">
          <div className="panel-title"><h2>"Saved you a wait"</h2></div>
          <Receipts receipts={st.receipts} />
        </section>
        <section className="panel lav">
          <div className="panel-title"><h2>Right-sizing tips</h2></div>
          {tips.length === 0 ? <p className="empty">No tips right now.</p> : (
            <ul className="tips">
              {tips.map((s) => (
                <li key={s.sid} onClick={() => openSession(s.sid)}>
                  <b>{short(s.name)}</b> {s.tip.text}
                  {s.tip.suggested_gib && <span className="pill accent"><i />Try {s.tip.suggested_gib} GiB</span>}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section className="panel">
        <div className="panel-title"><h2>All sessions</h2></div>
        <SessionsTable sessions={status.sessions} />
      </section>

      <section className="panel">
        <div className="panel-title"><h2>Event log</h2><span className="pill live"><i />Live</span></div>
        <ul className="log">
          {events.filter((e) => EVENT_TEXT[e.kind]).slice(0, 80).map((e) => (
            <li key={e.id} onClick={() => e.sid && openSession(e.sid)} className={e.sid ? "clickable" : ""}>
              <span className="mono muted">{clock(e.ts)}</span>
              <b>{short(e.user)}</b>
              <span>{EVENT_TEXT[e.kind]} <span className="muted">{eventExtra(e)}</span></span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

// A slot as a little VRAM board: 36 blocks (3 rows of 12) for the cap, filled up to the holder's usage.
function SlotCard({ slot, onOpen }) {
  const h = slot.holder;
  const capMib = slot.cap_gib * 1024;
  const blocks = 36;
  const used = h ? Math.round((h.mem_mib / capMib) * blocks) : 0;
  return (
    <article className={`slot-card ${h ? h.state : "free"}`} onClick={() => h && onOpen(h.sid)}>
      <div className="slot-top">
        <span className="eyebrow">Slot {slot.slot} · {slot.cap_gib} GiB</span>
        <StatePill state={h ? h.state : "free"} />
      </div>
      <h3>{h ? short(h.name) : "Free"}</h3>
      <div className="vram" aria-label={h ? `${h.mem_mib} of ${capMib.toFixed(0)} MiB` : "empty"}>
        {Array.from({ length: blocks }, (_, i) => <i key={i} className={i < used ? "on" : ""} style={{ animationDelay: `${(i % 12) * 0.06}s` }} />)}
      </div>
      {h ? (
        <div className="slot-stats">
          <span><b>{h.mem_mib}</b> MiB</span>
          <span><b>{h.util}%</b> compute</span>
          <span><b>{h.idle_s == null ? "–" : dur(h.idle_s)}</b> idle</span>
        </div>
      ) : <p className="muted">Next in the queue gets this slot.</p>}
      {h?.state === "warned" && (
        <div className="slot-warn">
          Parking in <b>{Math.max(0, Math.ceil(h.park_in_s))} s</b>
          <Action run={() => api.snooze(h.sid)} ghost={false}>Snooze ({h.snoozes_left})</Action>
        </div>
      )}
      {h?.snoozed_for_s != null && <p className="muted">Snoozed for {dur(h.snoozed_for_s)}</p>}
      {h && <span className="slot-open">Open controls ↑</span>}
    </article>
  );
}
