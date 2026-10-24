import { api } from "../api";
import useFetch from "../components/useFetch";
import MdTable from "../components/MdTable";
import PageHeader from "../components/PageHeader";
import { mean } from "../format";
import "./Results.css";

const CHARTS = [
  ["idle_gpu_hours.png", "GPU-hours held while idle, per policy"],
  ["wait_times.png", "Median and 95th-percentile wait, per policy"],
  ["sweep_idle_window.png", "GPU-hours reclaimed vs idle window"],
  ["sweep_load.png", "95th-percentile wait as demand grows"],
];

// Simulator results, live scenario summary, calibration and park benchmark.
export default function Results() {
  const { data: d, error } = useFetch(api.results, []);
  if (error) return <div className="page"><PageHeader eyebrow="Results" title="No results yet.">{error}</PageHeader></div>;
  if (!d) return <div className="page"><p className="empty">Loading…</p></div>;

  const names = Object.keys(d.main);
  const m = (n, k) => mean(d.main[n][k]);
  const hold = names[0];
  const ours = names.find((n) => n.includes("ours")) || names[names.length - 1];
  const wp = d.workload_params;
  const s = d.scenario;

  return (
    <div className="page results">
      <PageHeader eyebrow="Results" title="Four policies, one workload." soft="Parking wins on every column.">
        {wp.students} students sharing 8 GPUs for {wp.days} working days, {wp.sessions_per_student_day} notebook sessions per
        student per day plus Helios batch jobs. Mean of {d.seeds.length} seeds.
      </PageHeader>

      <dl className="cells">
        <div><dt>Idle GPU-hours a week</dt><dd>{m(hold, "idle_gpu_hours_held").toFixed(0)} → {m(ours, "idle_gpu_hours_held").toFixed(0)}</dd></div>
        <div><dt>p95 wait for a GPU</dt><dd>{m(hold, "wait_p95_min").toFixed(0)} → {m(ours, "wait_p95_min").toFixed(0)} <small>min</small></dd></div>
        <div><dt>Sessions with lost state</dt><dd>{m(ours, "sessions_state_lost").toFixed(0)} <small>vs {m(names[1], "sessions_state_lost").toFixed(0)} culled</small></dd></div>
        {s && <div><dt>Live park / restore</dt><dd>{s.b_park_s.toFixed(2)} / {s.b_restore_s.toFixed(2)} <small>s</small></dd></div>}
      </dl>

      <section className="panel" style={{ marginTop: 20 }}>
        <div className="panel-title"><h2>Simulated lab</h2></div>
        <div className="tablewrap">
          <table>
            <thead><tr>
              <th>Policy</th><th className="num">Idle GPU-h</th><th className="num">Reclaimed GPU-h</th><th className="num">Median wait</th>
              <th className="num">p95 wait</th><th className="num">Lost state</th><th className="num">Rerun GPU-h</th>
              <th className="num">Utilization</th><th className="num">Fairness</th>
            </tr></thead>
            <tbody>{names.map((n) => (
              <tr key={n} className={n === ours ? "ours" : ""}>
                <td>{n === ours && <span className="pill accent"><i />Ours</span>} {n}</td>
                <td className="num">{m(n, "idle_gpu_hours_held").toFixed(0)}</td>
                <td className="num">{m(n, "gpu_hours_reclaimed").toFixed(0)}</td>
                <td className="num">{m(n, "wait_median_min").toFixed(1)} min</td>
                <td className="num">{m(n, "wait_p95_min").toFixed(0)} min</td>
                <td className="num">{m(n, "sessions_state_lost").toFixed(0)} / {m(n, "sessions").toFixed(0)}</td>
                <td className="num">{m(n, "rerun_gpu_hours").toFixed(1)}</td>
                <td className="num">{m(n, "utilization_pct").toFixed(1)}%</td>
                <td className="num">{m(n, "jain_gpu_hours").toFixed(2)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </section>

      <div className="grid-2" style={{ marginTop: 20 }}>
        <SweepTable title="Idle window sweep" sub="minutes before warning" data={d.sweep} metric="gpu_hours_reclaimed" label="Reclaimed GPU-h" />
        <SweepTable title="Demand sweep" sub="sessions per student per day" data={d.load} metric="wait_p95_min" label="p95 wait (min)" />
      </div>

      <div className="charts">
        {CHARTS.map(([f, alt]) => (
          <figure key={f}><img src={`/files/${f}`} alt={alt} loading="lazy" /><figcaption className="eyebrow">{alt}</figcaption></figure>
        ))}
      </div>

      <div className="grid-2">
        <section className="panel lav">
          <div className="panel-title"><h2>Live mini-lab</h2><span className="eyebrow">RTX 3050 · 2 slots</span></div>
          {!s ? <p className="empty">No scenario run yet.</p> : (
            <dl className="facts">
              <div><dt>B parked (GPU → host RAM)</dt><dd>{s.b_park_s.toFixed(2)} s · {s.b_parked_gib} GiB</dd></div>
              <div><dt>B restored</dt><dd>{s.b_restore_s.toFixed(2)} s</dd></div>
              <div><dt>VRAM, both running → B parked</dt><dd>{s.vram_both_running_mib.toFixed(0)} → {s.vram_after_park_mib.toFixed(0)} MiB</dd></div>
              <div><dt>C waited for a slot</dt><dd>{s.c_waited_s.toFixed(1)} s</dd></div>
              <div><dt>Checksum before / parked / after</dt><dd>{s.checksum_match ? "Identical" : "MISMATCH"} <span className="mono muted">{s.b_checksum_before}</span></dd></div>
              <div><dt>Returning owner served before D</dt><dd>{s.returning_priority_worked ? "Yes" : "No"}</dd></div>
              <div><dt>VRAM back to baseline</dt><dd>{s.vram_back_to_baseline ? `Yes · ${s.vram_end_mib.toFixed(0)} MiB` : "No"}</dd></div>
              <div><dt>Scenario length</dt><dd>{s.duration_s.toFixed(0)} s · {s.started}</dd></div>
            </dl>
          )}
        </section>
        <section className="panel">
          <div className="panel-title"><h2>Calibration</h2><span className="eyebrow">vs NotebookOS</span></div>
          <table>
            <thead><tr><th>Statistic</th><th>Published</th><th className="num">Generated</th></tr></thead>
            <tbody>{d.calibration.map((c) => (
              <tr key={c.name}><td>{c.name}</td><td>{c.target}</td><td className="num">{c.achieved.toFixed(1)} {c.unit}</td></tr>
            ))}</tbody>
          </table>
        </section>
      </div>

      <section className="panel" style={{ marginTop: 20 }}>
        <div className="panel-title"><h2>Park / restore benchmark</h2></div>
        <div className="tablewrap"><MdTable md={d.park_bench_md} /></div>
      </section>
    </div>
  );
}

// data: {x: {policy: {metric: [per-seed values]}}}. Policy names can include the swept value, so one row per (x, policy).
function SweepTable({ title, sub, data, metric, label }) {
  if (!data) return null;
  const rows = Object.entries(data).flatMap(([x, byPolicy]) =>
    Object.entries(byPolicy).map(([p, r]) => [x, p, r[metric] ? mean(r[metric]) : null]));
  return (
    <section className="panel">
      <div className="panel-title"><h2>{title}</h2><span className="eyebrow">{sub}</span></div>
      <div className="tablewrap">
        <table>
          <thead><tr><th>Value</th><th>Policy</th><th className="num">{label}</th></tr></thead>
          <tbody>{rows.map(([x, p, v]) => (
            <tr key={`${x}-${p}`} className={p.includes("ours") ? "ours" : ""}>
              <td className="mono">{x}</td><td>{p}</td><td className="num">{v == null ? "–" : v.toFixed(1)}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </section>
  );
}
