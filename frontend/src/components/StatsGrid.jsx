import { dur, hours } from "../format";

// One user's impact stats (from /api/users/{user} or a leaderboard row).
export default function StatsGrid({ s }) {
  const items = [
    ["GPU time given back", hours(s.given_back_h)], ["People helped", s.people_helped],
    ["Wait saved for others", hours(s.wait_saved_for_others_h)], ["Wait saved for you", hours(s.wait_saved_for_you_h)],
    ["GPU time used", hours(s.gpu_h)], ["Sessions", s.sessions], ["Times parked", s.parks],
    ["Average wait", dur(s.avg_wait_s)], ["Snoozes used", s.snoozes], ["Peak memory", `${s.peak_gib} GiB`],
  ];
  return <dl className="cells">{items.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>;
}
