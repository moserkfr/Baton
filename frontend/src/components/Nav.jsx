import { useState } from "react";
import { href } from "../router";
import { useStatus } from "../status";
import { short } from "../format";
import "./Nav.css";

const LINKS = [
  ["lab", "Live lab"], ["user", "My page"], ["why", "Why parked?"], ["leaderboard", "Leaderboard"],
  ["notifications", "Inbox"], ["admin", "Admin"], ["results", "Results"],
];

export function Logo() {
  // The handoff: a block of GPU work carried from one slot to the next.
  return (
    <svg className="logo-mark" viewBox="0 0 24 24" aria-hidden="true">
      <rect className="slot" x="2" y="9.5" width="8.5" height="11" rx="2.2" />
      <rect className="slot" x="13.5" y="9.5" width="8.5" height="11" rx="2.2" />
      <rect className="block" x="15.5" y="13.25" width="4.5" height="5.25" rx="1.1" />
      <path className="arc" d="M6.25 7.5C6.25 2.8 17.75 2.8 17.75 6.6M15.6 5.3L17.75 7.5L19.9 5.3" />
    </svg>
  );
}

export default function Nav({ page, theme, myUser }) {
  const { online, status } = useStatus();
  const [open, setOpen] = useState(false);
  return (
    <header className="nav">
      <div className="nav-inner">
        <a className="brand" href="#/"><Logo /> Baton</a>
        <nav className={open ? "links open" : "links"} onClick={() => setOpen(false)}>
          {LINKS.map(([p, label]) => (
            <a key={p} href={href(p)} className={page === p ? "current" : ""}>{label}</a>
          ))}
        </nav>
        <div className="nav-right">
          <span className={`pill ${online ? "live" : "bad"}`} title={status?.gpu ? `GPU ${status.gpu.used_mib}/${status.gpu.total_mib} MiB` : ""}>
            <i />{online ? (status?.dry_run ? "Dry run" : "Online") : "Offline"}
          </span>
          {myUser && <a className="me" href={href("user", myUser)}>{short(myUser)}</a>}
          <button className="icon-btn" onClick={theme.toggle} aria-label="Toggle dark mode" title="Toggle dark mode">
            {theme.isDark ? (
              <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4.5" /><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8" /></svg>
            ) : (
              <svg viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z" /></svg>
            )}
          </button>
          <button className="icon-btn burger" onClick={() => setOpen(!open)} aria-label="Menu">
            <svg viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16" /></svg>
          </button>
        </div>
      </div>
      {status?.mode && (
        <div className="mode-banner">
          <span className="pill warn"><i />{status.mode.name}</span> Deadline mode is on: stricter idle window and grace period.
          <a href={href("admin")}>Details →</a>
        </div>
      )}
    </header>
  );
}
