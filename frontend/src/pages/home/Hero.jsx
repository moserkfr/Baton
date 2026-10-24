import { href } from "../../router";
import GpuCube from "./GpuCube";

// Section 1: headline, two actions, the animated GPU, and a strip of measured numbers.
export default function Hero({ facts }) {
  return (
    <section className="hero">
      <div className="hero-main">
        <div className="hero-copy">
          <h1>Idle GPUs,<br />given back.</h1>
          <p>
            Finds sessions that hold GPU memory but do no compute, parks them in host RAM with every tensor intact,
            and hands the GPU to the next student in a fair queue.
          </p>
          <div className="hero-actions">
            <a className="btn" href={href("lab")}>Open the live lab <span className="arrow">→</span></a>
            <a className="btn ghost" href={href("results")}>See the results</a>
          </div>
        </div>
        <GpuCube />
      </div>
      <div className="hero-strip">
        <div className="strip-group">
          <div className="eyebrow">Measured on the live mini-lab</div>
          <div className="strip-cells">
            <div><b>{facts.park}</b><span>to park a session</span></div>
            <div><b>{facts.restore}</b><span>to restore it</span></div>
            <div><b>{facts.checksum}</b><span>checksum after restore</span></div>
          </div>
        </div>
        <div className="strip-group narrow">
          <div className="eyebrow">Simulated 40-student lab</div>
          <div className="strip-cells">
            <div><b>{facts.idle}</b><span>idle GPU-hours a week</span></div>
          </div>
        </div>
      </div>
    </section>
  );
}
