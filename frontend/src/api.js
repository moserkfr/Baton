// Thin wrappers over the dashboard API (see API.md at the repo root).

async function request(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    // 503 = daemon not running, 400 = refused action; `detail` says why.
    throw new Error((data && data.detail) || `${res.status} ${res.statusText}`);
  }
  return data;
}

const enc = encodeURIComponent;

export const api = {
  status: () => request("GET", "/api/status"),
  events: (limit = 100, afterId = 0) => request("GET", `/api/events?limit=${limit}&after_id=${afterId}`),
  timeline: (sid) => request("GET", `/api/sessions/${enc(sid)}/timeline`),
  user: (user, days = 7) => request("GET", `/api/users/${enc(user)}?days=${days}`),
  notifications: (user = "", afterId = 0) =>
    request("GET", `/api/notifications?user=${enc(user)}&after_id=${afterId}`),
  leaderboard: (days = 7) => request("GET", `/api/leaderboard?days=${days}`),
  dryrunReport: (days) => request("GET", `/api/dryrun_report${days ? `?days=${days}` : ""}`),
  results: () => request("GET", "/api/results"),

  snooze: (session) => request("POST", `/api/snooze/${enc(session)}`),
  away: (session, body) => request("POST", `/api/away/${enc(session)}`, body),
  resume: (session) => request("POST", `/api/resume/${enc(session)}`),
  parkNow: (session) => request("POST", `/api/park_now/${enc(session)}`),
  setMode: (body) => request("POST", "/api/mode", body),
  clearMode: () => request("DELETE", "/api/mode"),
};
