import { useSheet } from "./sheet";
import StatePill from "./StatePill";
import { dur, hhmm, short } from "../format";

// Every live session; a row opens the session bottom sheet.
export default function SessionsTable({ sessions }) {
  const { openSession } = useSheet();
  if (!sessions.length) return <p className="empty">No sessions.</p>;
  return (
    <div className="tablewrap">
      <table>
        <thead><tr>
          <th>Session</th><th>User</th><th>State</th><th>Slot</th><th className="num">Memory</th><th className="num">Util</th>
          <th className="num">Idle</th><th className="num">GPU time</th><th className="num">Parked</th><th className="num">Parks</th>
        </tr></thead>
        <tbody>{sessions.map((s) => (
          <tr key={s.sid} className="clickable" onClick={() => openSession(s.sid)}>
            <td className="mono">{s.sid}</td>
            <td>{short(s.user)}</td>
            <td>
              <StatePill state={s.state} />
              {s.on_disk && <span className="muted"> · disk</span>}
              {s.back_at && <span className="muted"> · back {hhmm(s.back_at)}</span>}
            </td>
            <td>{s.slot ?? "–"}</td>
            <td className="num">{s.mem_mib} MiB</td>
            <td className="num">{s.util}%</td>
            <td className="num">{s.idle_s == null ? "–" : dur(s.idle_s)}</td>
            <td className="num">{dur(s.gpu_s)}</td>
            <td className="num">{dur(s.parked_total_s)}</td>
            <td className="num">{s.parks} / {s.restores}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}
