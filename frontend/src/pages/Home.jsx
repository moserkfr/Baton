import { api } from "../api";
import useFetch from "../components/useFetch";
import { mean } from "../format";
import Hero from "./home/Hero";
import Compare from "./home/Compare";
import DemandChart from "./home/DemandChart";
import Carousel from "./home/Carousel";
import "./Home.css";

// Used until /api/results loads (or if the simulator hasn't been run). Same numbers as the README.
const FALLBACK = {
  facts: { park: "0.28 s", restore: "0.10 s", checksum: "Identical", idle: "385 → 69" },
  cullerKept: 17,
  park: "0.28 s",
  xs: ["0.30 / day", "0.45 / day", "0.60 / day"],
  series: {
    wait: { label: "p95 wait", unit: "min", hold: [63.1, 263.5, 397.6], ours: [0, 0.2, 3.4] },
    idle: { label: "Idle GPU-hours", unit: "GPU-h", hold: [252.1, 385.4, 538.2], ours: [47.4, 69.4, 92.2] },
  },
};

function fromResults(d) {
  if (!d?.main) return FALLBACK;
  const names = Object.keys(d.main);
  const m = (n, k) => mean(d.main[n][k]);
  const hold = names[0], culler = names[1], ours = names.find((n) => n.includes("ours")) || names[names.length - 1];
  const s = d.scenario;
  const out = {
    facts: {
      park: s ? `${s.b_park_s.toFixed(2)} s` : FALLBACK.facts.park,
      restore: s ? `${s.b_restore_s.toFixed(2)} s` : FALLBACK.facts.restore,
      checksum: s ? (s.checksum_match ? "Identical" : "Mismatch") : FALLBACK.facts.checksum,
      idle: `${m(hold, "idle_gpu_hours_held").toFixed(0)} → ${m(ours, "idle_gpu_hours_held").toFixed(0)}`,
    },
    cullerKept: Math.round(100 * (1 - m(culler, "sessions_state_lost") / m(culler, "sessions"))),
    park: s ? `${s.b_park_s.toFixed(2)} s` : FALLBACK.park,
    xs: FALLBACK.xs,
    series: FALLBACK.series,
  };
  if (d.load) {
    const levels = Object.keys(d.load);
    const pick = (lvl, mine) => Object.entries(d.load[lvl]).find(([n]) => n.includes("ours") === mine)?.[1];
    const col = (mine, k) => levels.map((l) => mean(pick(l, mine)[k]));
    out.xs = levels.map((l) => `${Number(l).toFixed(2)} / day`);
    out.series = {
      wait: { label: "p95 wait", unit: "min", hold: col(false, "wait_p95_min"), ours: col(true, "wait_p95_min") },
      idle: { label: "Idle GPU-hours", unit: "GPU-h", hold: col(false, "idle_gpu_hours_held"), ours: col(true, "idle_gpu_hours_held") },
    };
  }
  return out;
}

// The landing page: hero, culler-vs-Baton, demand chart, feature carousel.
export default function Home() {
  const { data } = useFetch(api.results, []);
  const r = fromResults(data);
  return (
    <div className="home">
      <Hero facts={r.facts} />
      <Compare cullerKept={r.cullerKept} parkSeconds={r.park} />
      <DemandChart xs={r.xs} series={r.series} />
      <Carousel />
    </div>
  );
}
