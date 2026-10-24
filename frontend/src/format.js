export function dur(s) {
  if (s == null) return "–";
  if (s < 59.5) return `${s.toFixed(0)} s`;
  if (s < 3600) {
    const r = Math.round(s);   // round first, or 119.6 s would read "1 min 60 s"
    return `${Math.floor(r / 60)} min ${r % 60} s`;
  }
  return `${(s / 3600).toFixed(1)} h`;
}

export const hours = (h) => (h == null ? "–" : `${h.toFixed(2)} h`);
export const short = (u) => String(u || "").replace(/^student-/, "");
export const clock = (ts) => (ts ? new Date(ts * 1000).toLocaleTimeString() : "–");
export const hhmm = (ts) =>
  ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "–";
export const dateTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "–");
export const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;

export function eta(q) {
  if (q.eta_s == null) return "unknown";
  return `${q.eta_basis === "guess" ? "~" : ""}${dur(q.eta_s)}`;
}

// Short labels for the live event log. The timeline endpoint has full sentences.
export const EVENT_TEXT = {
  register: "logged in", grant: "got a slot", queued: "queued", idle: "went idle", active: "computing again",
  warn: "warned", warn_cancelled: "came back in time", park: "parking", parked: "parked",
  gave_slot: "slot given to the queue", owner_returned: "came back", restoring: "restoring",
  restored: "restored", receipt: "got a reclaimed slot", receipt_closed: "receipt closed", gone: "left",
  snoozed: "snoozed", away: "stepped away", prewarm: "restoring before return", spilled: "moved to disk",
  mode: "policy mode changed", notify: "notification",
  would_warn: "would warn (dry run)", would_park: "would park (dry run)", would_restore: "would restore (dry run)",
  park_now: "park requested", cannot_park: "cannot park", park_failed: "park failed", park_timeout: "park timed out",
};

export function eventExtra(e) {
  const d = e.detail || {};
  switch (e.kind) {
    case "parked": return d.gib != null ? `${d.gib} GiB in ${d.park_s?.toFixed(2)} s` : "";
    case "restored": return d.restore_s != null ? `in ${d.restore_s.toFixed(2)} s after waiting ${d.waited_s?.toFixed(1)} s` : "";
    case "grant": return d.waited_s > 0.5 ? `after waiting ${d.waited_s.toFixed(1)} s` : "";
    case "warn": return d.park_in_s != null ? `parking in ${d.park_in_s} s` : "";
    case "notify": return d.title || "";
    default: return "";
  }
}
