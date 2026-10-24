# Dashboard API (for building the frontend)

Base URL: `http://127.0.0.1:8000` (start it with `python -m baton.dashboard.app`).
Interactive docs with every request and response shape: **`/docs`** (FastAPI generates them).

Everything except the control endpoints is read from the daemon's SQLite file, so pages render even when the
daemon is stopped. Control endpoints return **503** if the daemon isn't running and **400** for a refused action
(the body's `detail` says why, e.g. `"no snoozes left today (3 per day)"`).

To try the frontend without a GPU: `python -m baton.dashboard.preview` (writes `data/preview.db`),
then `python -m baton.dashboard.app --db data/preview.db`.

## Live updates

| Endpoint | What |
| --- | --- |
| `GET /api/stream` | **Server-sent events.** `event: status` with the full status (below) whenever it changes (about 1/s), and `event: events` with an array of new log events, oldest first. |

```js
const es = new EventSource("/api/stream");
es.addEventListener("status", (e) => render(JSON.parse(e.data)));
es.addEventListener("events", (e) => JSON.parse(e.data).forEach(addToLog));   // includes notifications
```
Notifications arrive as events with `kind: "notify"`; filter by `user` to show a toast or a browser
`Notification` to the right student.

## Read endpoints

| Endpoint | Returns |
| --- | --- |
| `GET /api/status` | Full live status (see below), plus `online` (snapshot younger than 5 s) and `age_s`. |
| `GET /api/events?limit=100&after_id=0` | Event log, newest first. Each: `{id, ts, kind, sid, user, detail}`. |
| `GET /api/sessions/{sid}/timeline` | **"Why was I parked?"** `{sid, user, timeline: [{ts, kind, detail, text}]}`, oldest first, where `text` is a plain-English sentence. 404 if unknown. |
| `GET /api/users/{user}?days=7` | **"My page."** `{live_sessions, stats, notifications, receipts, past_sessions}`. |
| `GET /api/notifications?user=&after_id=0` | Inbox: events with `kind: "notify"`, `detail = {what, title, body}`. |
| `GET /api/leaderboard?days=7` | `{days, users: [stats...], totals}`, ranked by GPU time given back. |
| `GET /api/dryrun_report?days=` | What a dry run would have reclaimed: `{window_h, would_parks, users_affected, would_reclaim_gpu_h, longest_park_h, queue_wait_parking_could_serve_h, per_user, text}` (`text` is Markdown). |
| `GET /api/results` | Simulator results, park benchmark, scenario summary (for the results page). |

## Control endpoints

`{session}` is a session id (`s3`) or a user/name.

| Endpoint | Body | Effect |
| --- | --- | --- |
| `POST /api/snooze/{session}` | none | Withdraw the warning; no new warning for `policy.snooze_s`. Limited per day. Reply has `snoozes_left`. |
| `POST /api/away/{session}` | `{"minutes": 45}` or `{"until": <epoch s>}` | Park now; restored automatically `away_prewarm_s` before the return. Reply has `back_at`. |
| `POST /api/resume/{session}` | none | Bring a parked session back now (cancels an away timer). |
| `POST /api/park_now/{session}` | none | Park immediately (demo control). |
| `POST /api/mode` | `{"name": "exam week", "idle_window_s": 600, "grace_period_s": 120, "minutes": 600}` | Deadline mode: stricter policy, until cleared or for `minutes`. |
| `DELETE /api/mode` | none | Back to the normal policy. |

## The status object

```jsonc
{
  "time": 1761300000.0, "online": true, "dry_run": false, "parker": "cooperative",
  "gpu": {"used_mib": 2205, "total_mib": 6144, "util": 37, "temp_c": 52},
  "mode": null,                       // or {"name", "source": "manual"|"schedule", "until", "idle_window_s", "grace_period_s"}
  "policy": {"idle_window_s": 30, "grace_period_s": 10, "util_threshold_pct": 5, "snooze_s": 60,
             "snoozes_per_day": 3, "spill_after_s": 120},
  "slots": [{"slot": 0, "cap_gib": 2.2, "holder": <session or null>}],
  "queue": [{"position": 1, "sid": "s3", "user": "carol", "waiting_s": 12.0, "returning": false,
             "eta_s": 22.0, "eta_basis": "idle"}],    // basis: free|parking|warned|idle|guess|unknown; show "~" for guess
  "sessions": [<session>],
  "events": [<last 60 events>],
  "stats": {"reclaimed_gpu_s": 22.6, "parks": 1, "restores": 1,
            "dry_would_reclaim_s": 0, "dry_saved_wait_s": 0,
            "receipts": [{"beneficiary": "carol", "owner": "bob", "waited_s": 33.1, "saved_s": 10.1, "open": false}]}
}
```

A **session**:

```jsonc
{
  "sid": "s2", "user": "student-B", "name": "B", "pid": 1234,
  "state": "warned",            // queued | active | idle | warned | parking | parked | restoring
  "slot": 1, "mem_mib": 1152, "util": 0,
  "idle_s": 31.0,               // seconds without GPU work (active/idle/warned)
  "park_in_s": 7.0,             // countdown while warned
  "parked_s": null,             // seconds since parked
  "snoozed_for_s": null,        // snooze time left
  "snoozes_left": 3,
  "back_at": null,              // epoch s, when an "away" session will be restored
  "on_disk": false,             // parked state spilled to disk
  "dry_parked": false,          // dry run: would be parked right now
  "peak_mib": 600,
  "tip": {"kind": "oversized", "text": "You used at most 0.59 of your 2.20 GiB slot...", "suggested_gib": 0.75},
  "gpu_s": 85.0, "parked_total_s": 22.6, "parks": 1, "restores": 1,
  "last_park_s": 0.36, "last_restore_s": 0.06, "whitelist": false
}
```

A **user stats** row (`/api/users/{user}` → `stats`, `/api/leaderboard` → `users[]`):

```jsonc
{"user": "student-B", "sessions": 3, "gpu_h": 1.2, "given_back_h": 0.8, "parks": 2, "people_helped": 2,
 "wait_saved_for_others_h": 0.4, "wait_saved_for_you_h": 0.0, "avg_wait_s": 12.0, "snoozes": 1, "peak_gib": 0.6}
```

## Event kinds worth showing

`register, queued, grant, idle, active, warn, warn_cancelled, snoozed, away, park, parked, gave_slot, receipt,
receipt_closed, owner_returned, prewarm, restoring, restored, spilled, gone, mode, notify,
would_warn, would_park, would_restore` (dry run). The timeline endpoint already turns each into a sentence.

Notification `what` values: `warn, parked, ready, restored, next, spilled`.
