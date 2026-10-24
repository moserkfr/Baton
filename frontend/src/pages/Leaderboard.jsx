import { useState } from "react";
import { api } from "../api";
import useFetch from "../components/useFetch";
import PageHeader from "../components/PageHeader";
import { href } from "../router";
import { dur, hours, short } from "../format";
import "./Leaderboard.css";

// Users ranked by GPU time given back, plus lab totals.
export default function Leaderboard() {
  const [days, setDays] = useState(7);
  const { data, error } = useFetch(() => api.leaderboard(days), [days], 10000);
  const top = data?.users?.[0]?.given_back_h || 0;

  return (
    <div className="page board">
      <PageHeader eyebrow="Leaderboard" title="Who gave the most back." soft="Ranked by GPU time returned to the lab.">
        Parking is a favour to whoever is next in the queue. This is the scoreboard.
      </PageHeader>

      <div className="toolbar" style={{ marginBottom: 20 }}>
        <div className="seg">
          {[1, 7, 30].map((d) => (
            <button key={d} className={days === d ? "on" : ""} onClick={() => setDays(d)}>{d === 1 ? "Today" : `${d} days`}</button>
          ))}
        </div>
      </div>
      {error && <p className="error">{error}</p>}

      {data && (
        <div className="stack">
          <dl className="cells">
            <div><dt>GPU time given back</dt><dd>{hours(data.totals.given_back_h)}</dd></div>
            <div><dt>Wait saved for others</dt><dd>{hours(data.totals.wait_saved_for_others_h)}</dd></div>
            <div><dt>People helped</dt><dd>{data.totals.people_helped}</dd></div>
            <div><dt>Parks</dt><dd>{data.totals.parks}</dd></div>
            <div><dt>Sessions</dt><dd>{data.totals.sessions}</dd></div>
            <div><dt>GPU time used</dt><dd>{hours(data.totals.gpu_h)}</dd></div>
          </dl>

          <section className="panel">
            {data.users.length === 0 ? <p className="empty">No finished sessions in this window.</p> : (
              <ol className="ranks">
                {data.users.map((u, i) => (
                  <li key={u.user}>
                    <span className={`rank r${i + 1}`}>{String(i + 1).padStart(2, "0")}</span>
                    <a href={href("user", u.user)} className="rank-name">{short(u.user)}</a>
                    <div className="rank-bar"><i style={{ width: `${top ? (100 * u.given_back_h) / top : 0}%` }} /></div>
                    <span className="rank-main num">{hours(u.given_back_h)}</span>
                    <span className="rank-meta muted">
                      helped {u.people_helped} · saved others {hours(u.wait_saved_for_others_h)} · {u.parks} parks · avg wait {dur(u.avg_wait_s)}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
