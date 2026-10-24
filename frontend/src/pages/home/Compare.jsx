import { useEffect, useState } from "react";
import useReveal from "../../components/useReveal";

const ITEMS = [
  ["weights.pt", "model weights"],
  ["adam_state.pt", "optimizer state"],
  ["dataloader.cursor", "epoch 3, batch 412"],
  ["rng_state.bin", "random seeds"],
  ["notebook.vars", "every variable"],
];

// Count up to `to` once visible.
function useCount(to, on, ms = 1200) {
  const [v, setV] = useState(0);
  useEffect(() => {
    if (!on) return;
    let raf;
    const t0 = performance.now();
    const step = (now) => {
      const k = Math.min(1, (now - t0) / ms);
      setV(Math.round(to * (1 - (1 - k) ** 3)));
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [to, on, ms]);
  return v;
}

// Section 3: activity culler (kills the notebook) vs Baton (parks it), side by side.
export default function Compare({ cullerKept, parkSeconds }) {
  const [ref, seen] = useReveal();
  const left = useCount(cullerKept, seen);
  const right = useCount(100, seen);

  return (
    <section className={`compare reveal ${seen ? "in" : ""}`} ref={ref}>
      <div className="section-head">
        <h2>A culler frees the GPU by killing your notebook. <span className="soft">Baton frees it and keeps your work.</span></h2>
        <p>
          A parked session keeps running: its GPU memory moves to host RAM and the slot goes to the next student.
          When the owner comes back, every tensor and optimizer state is restored.
        </p>
      </div>
      <div className="compare-grid">
        <div className="compare-panel stale">
          <div className="tags"><span className="pill"><i />Activity culler</span><span className="pill bad"><i />Kills</span></div>
          <h3>A culler frees the slot once.</h3>
          <p className="muted">After 30 idle minutes, the notebook is gone.</p>
          <div className="state-card">
            <div className="state-head">State kept: <span className="pct">{left}%</span></div>
            {ITEMS.map(([file, what], i) => (
              <div key={file} className="file-row lost" style={{ opacity: 1 - i * 0.16 }}>
                <span className="file">{file}</span><span className="mono muted">LOST · RERUN</span>
              </div>
            ))}
          </div>
        </div>

        <div className="compare-panel live">
          <div className="tags"><span className="pill accent"><i />Baton</span><span className="pill live"><i />Parks</span></div>
          <h3>Baton parks it instead.</h3>
          <p className="muted">GPU memory → host RAM in {parkSeconds}. Nothing lost.</p>
          <div className="state-card">
            <div className="state-head">State kept: <span className="pct hi">{right}%</span></div>
            {ITEMS.map(([file, what], i) => (
              <div key={file} className="file-row" style={{ animationDelay: `${i * 0.12}s` }}>
                <span className="file">{file}</span><span className="mono muted">{what.toUpperCase()}</span>
              </div>
            ))}
          </div>
          <div className="float-tile t1" aria-hidden="true">
            <svg viewBox="0 0 24 24"><rect x="5" y="5" width="14" height="14" rx="2" /><rect x="9" y="9" width="6" height="6" rx="1" /><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3" /></svg>
          </div>
          <div className="float-tile t2" aria-hidden="true">
            <svg viewBox="0 0 24 24"><rect x="2" y="7" width="20" height="9" rx="1.5" /><path d="M6 16v3M10 16v3M14 16v3M18 16v3M6 10h2v3H6zM11 10h2v3h-2zM16 10h2v3h-2z" /></svg>
          </div>
        </div>
      </div>
    </section>
  );
}
