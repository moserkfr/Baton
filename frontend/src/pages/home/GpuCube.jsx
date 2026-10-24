import { useEffect, useState } from "react";

// Isometric GPU: two slot layers of VRAM blocks above a host-RAM slab, inside a wireframe cube.
// A scripted loop tells Baton's story: student B goes idle, is warned, its blocks drop into
// host RAM (parked), C borrows the slot, then B's blocks rise back (restored).

const G = 7;                    // cells per side
const U = 26;                   // px per cell
const Z = { ram: 0, s0: 3.3, s1: 6.6, top: 8.6 };
const SLAB = 0.45;
const CYCLE = 14;

// Phases of the loop (seconds). `ruler` is the label the NOW marker points at.
const PHASES = [
  { until: 3.0, name: "active", ruler: 0, line: "B is training · 85% compute" },
  { until: 5.0, name: "idle", ruler: 1, line: "B holds 1.1 GiB · 0% compute for 30 s" },
  { until: 6.6, name: "warned", ruler: 2, line: "B warned · parking in 10 s unless GPU work starts" },
  { until: 7.8, name: "parking", ruler: 3, line: "Parking B · GPU memory → host RAM" },
  { until: 10.6, name: "parked", ruler: 3, line: "B parked in 0.28 s · slot 1 handed to C" },
  { until: 11.3, name: "handback", ruler: 3, line: "C is done · slot 1 free for its owner" },
  { until: 12.6, name: "restoring", ruler: 4, line: "Restoring B · host RAM → GPU" },
  { until: CYCLE, name: "restored", ruler: 4, line: "B restored in 0.10 s · checksum identical" },
];
const RULER = ["ACTIVE", "IDLE 30 S", "WARNED", "PARKED", "RESTORED"];

const iso = (x, y, z) => [(x - y) * U * 0.866, (x + y) * U * 0.5 - z * U];
const pt = (p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`;
const rhombus = (z, inset = 0) =>
  [iso(inset, inset, z), iso(G - inset, inset, z), iso(G - inset, G - inset, z), iso(inset, G - inset, z)].map(pt).join(" ");
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
const clamp01 = (v) => Math.max(0, Math.min(1, v));

// Cells that hold memory: a rounded blob, denser in the middle (like the reference's dot clusters).
function cells(seed, fill) {
  const out = [];
  const c = (G - 1) / 2;
  for (let i = 0; i < G; i++) {
    for (let j = 0; j < G; j++) {
      const d = Math.hypot(i - c, j - c) / (c * 1.42);
      const noise = (Math.sin((i + 1) * 12.9898 * seed + (j + 1) * 78.233) * 43758.5453) % 1;
      if (Math.abs(noise) > d * 0.95 * (1.25 - fill)) out.push({ i, j, d, k: out.length });
    }
  }
  return out;
}
const A_CELLS = cells(1.7, 0.75);
const B_CELLS = cells(2.3, 0.85);
const C_CELLS = cells(3.1, 0.6);

function phaseAt(t) {
  let start = 0;
  for (const p of PHASES) {
    if (t < p.until) return { ...p, start, p: (t - start) / (p.until - start) };
    start = p.until;
  }
  return { ...PHASES[0], start: 0, p: 0 };
}

export default function GpuCube() {
  const [t, setT] = useState(1.2);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let raf, last = 0;
    const t0 = performance.now() - 1200;
    const loop = (now) => {
      if (now - last > 33) { last = now; setT(((now - t0) / 1000) % CYCLE); }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  const ph = phaseAt(t);
  const pulse = (d, speed = 1.3) => 1 + 0.32 * Math.sin(2 * Math.PI * (t * speed - d * 1.4));

  // B's blocks: height and look depend on the phase; each block drops/rises with its own stagger.
  const bDot = (c) => {
    const stagger = (c.k % 9) / 9 * 0.45;
    let z = Z.s1, cls = "dot-b", r = 3.4;
    if (ph.name === "active" || ph.name === "restored") r *= pulse(c.d);
    if (ph.name === "idle") cls = "dot-idle";
    if (ph.name === "warned") cls = Math.floor(t * 4) % 2 ? "dot-warn" : "dot-idle";
    if (ph.name === "parking") {
      const k = ease(clamp01((ph.p - stagger) / (1 - 0.45)));
      z = Z.s1 + (Z.ram + SLAB - Z.s1) * k; cls = k > 0.98 ? "dot-ram" : "dot-move";
    }
    if (ph.name === "parked" || ph.name === "handback") { z = Z.ram + SLAB; cls = "dot-ram"; }
    if (ph.name === "restoring") {
      const k = ease(clamp01((ph.p - stagger) / (1 - 0.45)));
      z = Z.ram + SLAB + (Z.s1 - Z.ram - SLAB) * k; cls = k > 0.98 ? "dot-b" : "dot-move";
    }
    return { z, cls, r };
  };

  // C borrows slot 1 while B is parked.
  let cOpacity = 0;
  if (ph.name === "parked") cOpacity = clamp01(ph.p * 4);
  if (ph.name === "handback") cOpacity = 1 - ph.p;

  const dot = (c, z, cls, r, key, opacity = 1) => {
    const [x, y] = iso(c.i + 0.5, c.j + 0.5, z);
    return <ellipse key={key} cx={x} cy={y} rx={r * 1.25} ry={r * 0.72} className={cls} opacity={opacity} />;
  };

  const rulerX = 228, rulerTop = -190, rulerStep = 82;
  const markerY = rulerTop + ph.ruler * rulerStep;
  const corner = (x, y, z) => pt(iso(x, y, z));

  return (
    <figure className="gpu-cube">
      <svg viewBox="-290 -240 615 455" role="img" aria-label="Animated GPU: a session's memory is parked to host RAM and restored">
        {/* back edges of the wireframe */}
        <g className="wire">
          <polyline points={[corner(0, 0, Z.ram), corner(0, 0, Z.top)].join(" ")} />
          <polyline points={[corner(G, 0, Z.top), corner(0, 0, Z.top), corner(0, G, Z.top)].join(" ")} />
          <polyline points={[corner(G, 0, Z.ram), corner(0, 0, Z.ram), corner(0, G, Z.ram)].join(" ")} className="faint" />
        </g>

        {/* host RAM slab */}
        <g className="slab">
          <polygon points={[corner(0, G, 0), corner(G, G, 0), corner(G, G, SLAB), corner(0, G, SLAB)].join(" ")} className="side-l" />
          <polygon points={[corner(G, 0, 0), corner(G, G, 0), corner(G, G, SLAB), corner(G, 0, SLAB)].join(" ")} className="side-r" />
          <polygon points={rhombus(SLAB)} className="top" />
        </g>
        <text className="layer-label" {...xy(iso(0, G, SLAB), -12, 4)}>HOST RAM</text>

        {/* slot 0: student A, always busy */}
        <polygon points={rhombus(Z.s0)} className="plane" />
        {A_CELLS.map((c) => dot(c, Z.s0, "dot-a", 3.1 * pulse(c.d, 1.05), `a${c.k}`))}
        <text className="layer-label" {...xy(iso(0, G, Z.s0), -12, 4)}>SLOT 0 · A</text>

        {/* slot 1: student B (and C while B is parked) */}
        <polygon points={rhombus(Z.s1)} className={`plane ${ph.name === "warned" ? "plane-warn" : ""}`} />
        {C_CELLS.map((c) => dot(c, Z.s1, "dot-c", 3.1 * pulse(c.d, 1.6), `c${c.k}`, cOpacity))}
        {B_CELLS.map((c) => { const b = bDot(c); return dot(c, b.z, b.cls, b.r, `b${c.k}`); })}
        <text className="layer-label" {...xy(iso(0, G, Z.s1), -12, 4)}>
          SLOT 1 · {cOpacity > 0.5 ? "C" : "B"}
        </text>

        {/* front edges */}
        <g className="wire">
          <polyline points={[corner(G, 0, Z.ram), corner(G, 0, Z.top)].join(" ")} />
          <polyline points={[corner(0, G, Z.ram), corner(0, G, Z.top)].join(" ")} />
          <polyline points={[corner(G, G, Z.ram), corner(G, G, Z.top)].join(" ")} />
          <polyline points={[corner(G, 0, Z.top), corner(G, G, Z.top), corner(0, G, Z.top)].join(" ")} />
        </g>

        {/* state ruler, like the reference's time scale */}
        <g className="ruler">
          <line x1={rulerX} y1={rulerTop - 14} x2={rulerX} y2={rulerTop + 4 * rulerStep + 14} />
          {Array.from({ length: 4 * 6 + 1 }, (_, i) => (
            <line key={i} x1={rulerX} x2={rulerX + (i % 6 ? 5 : 10)} y1={rulerTop + (i * rulerStep) / 6} y2={rulerTop + (i * rulerStep) / 6} />
          ))}
          {RULER.map((label, i) => (
            <text key={label} x={rulerX + 18} y={rulerTop + i * rulerStep + 3.5} className={i === ph.ruler ? "on" : ""}>{label}</text>
          ))}
          <line className="connector" x1={iso(G, 0, 0)[0] + 4} x2={rulerX - 4} y1={markerY} y2={markerY} />
          <g transform={`translate(${rulerX - 2}, ${markerY})`} className="marker">
            <rect x={-3} y={-9} width={6} height={18} rx={1.5} />
          </g>
        </g>
      </svg>
      <figcaption className="mono">
        <span className={`pill ${ph.name === "warned" ? "warn" : ph.name === "idle" ? "bad" : "accent"}`}><i />{ph.name}</span>
        {ph.line}
      </figcaption>
    </figure>
  );
}

function xy([x, y], dx, dy) {
  return { x: x + dx, y: y + dy, textAnchor: "end" };
}
