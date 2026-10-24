import { clock } from "../format";
import "./TimelineList.css";

const TONE = {
  warn: "warn", idle: "bad", parked: "accent", park: "accent", restored: "good", grant: "good",
  restoring: "accent", spilled: "accent", snoozed: "warn", away: "accent", gone: "", notify: "",
};

// A session's events as plain-English sentences, oldest first.
export default function TimelineList({ timeline }) {
  if (!timeline.length) return <p className="empty">No events.</p>;
  return (
    <ol className="tl">
      {timeline.map((ev, i) => (
        <li key={ev.id ?? i} className={TONE[ev.kind] || ""}>
          <span className="tl-dot" />
          <div>
            <div className="tl-meta"><span className="mono">{clock(ev.ts)}</span><span className="mono muted">{ev.kind}</span></div>
            <div>{ev.text}</div>
          </div>
        </li>
      ))}
    </ol>
  );
}
