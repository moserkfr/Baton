import { useState } from "react";
import { api } from "../api";
import Action from "../components/Action";
import useFetch from "../components/useFetch";
import PageHeader from "../components/PageHeader";
import { useStatus } from "../status";
import { href } from "../router";
import { dateTime, dur, hours, short } from "../format";
import "./Admin.css";

// Lab admin: current policy, deadline mode, and the dry-run report.
export default function Admin() {
  const { status } = useStatus();
  return (
    <div className="page admin">
      <PageHeader eyebrow="Admin" title="Set the rules once." soft="Tighten them for exam week.">
        The policy every session is judged by, a switch for deadline mode, and what a dry run says parking would reclaim.
      </PageHeader>
      <div className="stack">
        <PolicyCard status={status} />
        <ModeCard status={status} />
        <DryRunCard />
      </div>
    </div>
  );
}

function PolicyCard({ status }) {
  const p = status?.policy;
  return (
    <section>
      <div className="eyebrow section-label">Current policy{status?.mode ? ` · ${status.mode.name}` : ""}</div>
      {!p ? <p className="empty">No snapshot yet.</p> : (
        <dl className="cells">
          <div><dt>Idle window</dt><dd>{dur(p.idle_window_s)}</dd></div>
          <div><dt>Grace period</dt><dd>{dur(p.grace_period_s)}</dd></div>
          <div><dt>Idle below</dt><dd>{p.util_threshold_pct}% <small>utilization</small></dd></div>
          <div><dt>Snooze length</dt><dd>{dur(p.snooze_s)}</dd></div>
          <div><dt>Snoozes per day</dt><dd>{p.snoozes_per_day}</dd></div>
          <div><dt>Spill to disk after</dt><dd>{!p.spill_after_s ? "Never" : dur(p.spill_after_s)}</dd></div>
          <div><dt>Parker</dt><dd>{status.parker}</dd></div>
          <div><dt>Dry run</dt><dd>{status.dry_run ? "On" : "Off"}</dd></div>
        </dl>
      )}
      <p className="muted small">Defaults live in config.yaml; scheduled modes go under <code>policy.schedules</code>.</p>
    </section>
  );
}

function ModeCard({ status }) {
  const [name, setName] = useState("exam week");
  const [idle, setIdle] = useState(600);
  const [grace, setGrace] = useState(120);
  const [minutes, setMinutes] = useState("");
  const mode = status?.mode;
  const num = (v) => (v === "" ? null : Number(v));

  return (
    <section className={`mode-card ${mode ? "on" : ""}`}>
      <div className="mode-copy">
        <span className={`pill ${mode ? "warn" : ""}`}><i />{mode ? `${mode.name} · ${mode.source}` : "Normal policy"}</span>
        <h2>Deadline mode</h2>
        {mode ? (
          <>
            <p>Idle window {dur(mode.idle_window_s)}, grace {dur(mode.grace_period_s)}, {mode.until ? `until ${dateTime(mode.until)}` : "until cleared"}.</p>
            <Action run={api.clearMode} ghost={false} small={false}>Back to normal</Action>
          </>
        ) : <p>A stricter idle window and grace period while everyone needs a GPU at once.</p>}
      </div>
      <form className="mode-form" onSubmit={(e) => e.preventDefault()}>
        <label className="field">Name<input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="field">Idle window (s)<input type="number" value={idle} onChange={(e) => setIdle(e.target.value)} /></label>
        <label className="field">Grace period (s)<input type="number" value={grace} onChange={(e) => setGrace(e.target.value)} /></label>
        <label className="field">Lasts (min)<input type="number" value={minutes} onChange={(e) => setMinutes(e.target.value)} placeholder="until cleared" /></label>
        <Action
          run={() => api.setMode({ name, idle_window_s: num(idle), grace_period_s: num(grace), minutes: num(minutes) })}
          ghost={false} small={false}
        >
          Start deadline mode →
        </Action>
      </form>
    </section>
  );
}

function DryRunCard() {
  const [days, setDays] = useState("");
  const { data, error } = useFetch(() => api.dryrunReport(days ? Number(days) : undefined), [days], 15000);
  return (
    <section className="panel">
      <div className="panel-title">
        <h2>Dry-run report</h2>
        <label className="field inline">Last<input type="number" min="1" value={days} onChange={(e) => setDays(e.target.value)} placeholder="all" style={{ width: 70 }} />days</label>
      </div>
      <p className="muted small">Run the daemon with <code>--dry-run</code> to see what parking would do before it parks anyone.</p>
      {error && <p className="error">{error}</p>}
      {data && (
        <>
          {!data.daemon_in_dry_run && <p><span className="pill"><i />The daemon is not in dry-run mode right now.</span></p>}
          <dl className="cells">
            <div><dt>Window observed</dt><dd>{hours(data.window_h)}</dd></div>
            <div><dt>Would-be parks</dt><dd>{data.would_parks}</dd></div>
            <div><dt>Users affected</dt><dd>{data.users_affected}</dd></div>
            <div><dt>Would reclaim</dt><dd>{hours(data.would_reclaim_gpu_h)}</dd></div>
            <div><dt>Longest park</dt><dd>{hours(data.longest_park_h)}</dd></div>
            <div><dt>Queue wait it could serve</dt><dd>{hours(data.queue_wait_parking_could_serve_h)}</dd></div>
          </dl>
          {data.per_user.length > 0 && (
            <div className="tablewrap" style={{ marginTop: 16 }}>
              <table>
                <thead><tr><th>User</th><th className="num">Would-be parks</th><th className="num">Would reclaim</th></tr></thead>
                <tbody>{data.per_user.map((r) => (
                  <tr key={r.user}>
                    <td><a href={href("user", r.user)}>{short(r.user)}</a></td>
                    <td className="num">{r.would_parks}</td>
                    <td className="num">{hours(r.would_reclaim_h)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
          <details className="md">
            <summary>Report as Markdown</summary>
            <pre>{data.text}</pre>
          </details>
        </>
      )}
    </section>
  );
}
