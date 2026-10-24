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
  // A relay baton in flight, its violet grip band the GPU being handed on.
  return (
    <svg className="logo-mark" viewBox="0 0 24 24" aria-hidden="true">
      <g transform="rotate(-40 12 12)">
        <rect className="body" x="6" y="8.75" width="15.5" height="6.5" rx="3.25" />
        <rect className="band" x="12.5" y="8.75" width="3" height="6.5" />
      </g>
      <path className="trail" d="M4.86 15.64L2.03 18.02M7.18 18.4L4.35 20.78" />
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
