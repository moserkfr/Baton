import { useState } from "react";
import useReveal from "../../components/useReveal";

const W = 520, H = 280, PAD = { l: 8, r: 24, t: 16, b: 36 };

// Smooth path through points (Catmull-Rom converted to cubic Béziers).
function smooth(pts) {
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${c1[0]},${c1[1]} ${c2[0]},${c2[1]} ${p2[0]},${p2[1]}`;
  }
  return d;
}

// Section 6: as demand grows, holding GPUs until logout makes the queue explode; parking keeps it flat.
// `series` = {metric: {label, unit, hold: [..], ours: [..]}}, `xs` = demand levels.
export default function DemandChart({ xs, series }) {
  const [metric, setMetric] = useState("wait");
  const [hover, setHover] = useState(null);
  const [ref, seen] = useReveal();
  const s = series[metric];
  const max = Math.max(...s.hold, ...s.ours) * 1.08 || 1;
  const x = (i) => PAD.l + (i * (W - PAD.l - PAD.r)) / (xs.length - 1);
  const y = (v) => H - PAD.b - (v / max) * (H - PAD.t - PAD.b);
  const holdPts = s.hold.map((v, i) => [x(i), y(v)]);
  const oursPts = s.ours.map((v, i) => [x(i), y(v)]);

  return (
    <section className={`demand reveal ${seen ? "in" : ""}`} ref={ref}>
      <div className="demand-copy">
        <h2>
          <span className="soft">More students, much longer waits.</span><br />
          Unless idle GPUs are given back.
        </h2>
        <p>
          The same simulated lab at three levels of demand. Holding a GPU until logout lets the queue grow
          by hours. Parking idle sessions keeps the wait close to zero.
        </p>
        <div className="seg" role="tablist">
          {Object.entries(series).map(([k, v]) => (
            <button key={k} role="tab" aria-selected={metric === k} className={metric === k ? "on" : ""} onClick={() => setMetric(k)}>
              {v.label}
            </button>
          ))}
        </div>
      </div>

      <div className="demand-chart">
        <div className="legend">
          <span><i className="hold" />Hold until logout</span>
          <span><i className="ours" />Park and restore</span>
        </div>
        <svg viewBox={`0 0 ${W} ${H}`} key={metric} onMouseLeave={() => setHover(null)}>
          <defs>
            <marker id="arrow-hold" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" className="arrow-hold" />
            </marker>
            <marker id="arrow-ours" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" className="arrow-ours" />
            </marker>
          </defs>
          {Array.from({ length: 13 }, (_, i) => {
            const gx = PAD.l + (i * (W - PAD.l - PAD.r)) / 12;
            return <line key={i} className="grid" x1={gx} x2={gx} y1={PAD.t} y2={H - PAD.b} />;
          })}
          <path d={smooth(holdPts)} className={`line hold ${seen ? "draw" : ""}`} pathLength="1" markerEnd="url(#arrow-hold)" />
          <path d={smooth(oursPts)} className={`line ours ${seen ? "draw" : ""}`} pathLength="1" markerEnd="url(#arrow-ours)" />
          {xs.map((label, i) => (
            <g key={label}>
              <rect x={x(i) - 40} y={PAD.t} width={80} height={H - PAD.t - PAD.b} fill="transparent" onMouseEnter={() => setHover(i)} />
              <line className="tick" x1={x(i) - 36} x2={x(i) + 36} y1={H - PAD.b + 8} y2={H - PAD.b + 8} />
              <text x={x(i)} y={H - 8} textAnchor="middle" className="xlabel">{label}</text>
              {hover === i && (
                <>
                  <line className="hover-line" x1={x(i)} x2={x(i)} y1={PAD.t} y2={H - PAD.b} />
                  <circle cx={x(i)} cy={y(s.hold[i])} r="4" className="pt hold" />
                  <circle cx={x(i)} cy={y(s.ours[i])} r="4" className="pt ours" />
                </>
              )}
            </g>
          ))}
        </svg>
        <div className="readout mono">
          {hover == null ? "Hover a demand level" : (
            <>{xs[hover]} · hold <b>{s.hold[hover].toFixed(0)} {s.unit}</b> · park <b>{s.ours[hover].toFixed(1)} {s.unit}</b></>
          )}
        </div>
      </div>
    </section>
  );
}
