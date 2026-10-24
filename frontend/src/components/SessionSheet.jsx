import { api } from "../api";
import { useStatus } from "../status";
import { href } from "../router";
import { dur, hhmm, short } from "../format";
import Action from "./Action";
import AwayForm from "./AwayForm";
import StatePill from "./StatePill";
import TimelineList from "./TimelineList";
import useFetch from "./useFetch";

// Bottom-sheet content for one session: live numbers, every control, and its "why" timeline.
export default function SessionSheet({ sid }) {
  const { status } = useStatus();
  const s = status?.sessions?.find((x) => x.sid === sid);
  const slot = status?.slots?.find((x) => x.holder?.sid === sid);
  const { data, error } = useFetch(() => api.timeline(sid), [sid], 3000);
  const capMib = slot ? slot.cap_gib * 1024 : null;

  return (
    <div>
      <div className="eyebrow"><span className="dot" />Session {sid}</div>
      <div className="ss-head" style={{ marginTop: 10 }}>
        <h2>{s ? short(s.name) : data ? short(data.user) : sid}</h2>
        {s && <StatePill state={s.state} />}
        {s?.whitelist && <span className="pill"><i />Whitelisted</span>}
        {s?.dry_parked && <span className="pill accent"><i />Dry run: would park</span>}
      </div>
      <div className="ss-sub">
        {s ? <>{s.user} · pid {s.pid} · {s.slot != null ? `slot ${s.slot}` : "no slot"}</> : "This session has ended."}
        {(s?.user || data?.user) && <> · <a href={href("user", s?.user || data.user)}>Open their page →</a></>}
      </div>

      {s && (
        <>
          <dl className="cells">
            <div>
              <dt>GPU memory</dt>
              <dd>{s.mem_mib} <small>MiB{capMib ? ` / ${capMib.toFixed(0)}` : ""}</small></dd>
              {capMib && <div className="ss-mem"><i style={{ width: `${Math.min(100, (100 * s.mem_mib) / capMib)}%` }} /></div>}
            </div>
            <div><dt>Compute</dt><dd>{s.util}%</dd></div>
            <div><dt>No GPU work for</dt><dd>{s.idle_s == null ? "–" : dur(s.idle_s)}</dd></div>
            <div><dt>Peak memory</dt><dd>{s.peak_mib} <small>MiB</small></dd></div>
            <div><dt>GPU time</dt><dd>{dur(s.gpu_s)}</dd></div>
            <div><dt>Parked total</dt><dd>{dur(s.parked_total_s)}</dd></div>
            <div><dt>Last park / restore</dt><dd>{s.last_park_s?.toFixed(2)} <small>/</small> {s.last_restore_s?.toFixed(2)} <small>s</small></dd></div>
            <div><dt>Snoozes left today</dt><dd>{s.snoozes_left}</dd></div>
          </dl>

          {s.state === "warned" && (
            <div className="ss-countdown">
              Parking in <b>{Math.max(0, Math.ceil(s.park_in_s))} s</b> unless GPU work starts.
              <Action run={() => api.snooze(sid)} ghost={false}>Snooze ({s.snoozes_left} left)</Action>
            </div>
          )}
          {s.snoozed_for_s != null && <p className="muted">Snoozed: no warning for another {dur(s.snoozed_for_s)}.</p>}
          {s.state === "parked" && (
            <p className="muted">
              Parked {dur(s.parked_s)} ago{s.on_disk ? ", spilled to disk" : ", in host RAM"}
              {s.back_at ? `. Comes back at ${hhmm(s.back_at)}.` : "."}
            </p>
          )}

          <div className="ss-actions">
            {s.state === "parked"
              ? <Action run={() => api.resume(sid)} ghost={false}>Bring it back now</Action>
              : <>
                <Action run={() => api.snooze(sid)}>Snooze</Action>
                <Action run={() => api.parkNow(sid)} title="Demo control">Park now</Action>
              </>}
            <AwayForm session={sid} />
          </div>

          {s.tip && (
            <div className="ss-tip">
              <div className="eyebrow">Right-sizing tip</div>
              {s.tip.text}{s.tip.suggested_gib && <b> Try {s.tip.suggested_gib} GiB.</b>}
            </div>
          )}
        </>
      )}

      <div className="ss-section">
        <h3>Why was I parked?</h3>
        {error && <p className="error">{error}</p>}
        {data && <TimelineList timeline={data.timeline} />}
      </div>
    </div>
  );
}
