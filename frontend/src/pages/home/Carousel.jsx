import { useEffect, useState } from "react";
import { href } from "../../router";
import useReveal from "../../components/useReveal";
import Scene from "./Scene";

const ITEMS = [
  { tag: "Parking", title: "Park and restore.", scene: "park", to: "lab",
    text: "A session idle past the window is warned, then its GPU memory moves to host RAM in a fraction of a second. The process keeps running, and it comes back with every tensor in place." },
  { tag: "Scheduling", title: "A fair queue with an ETA.", scene: "queue", to: "lab",
    text: "Students waiting for a GPU see their position and an honest estimate. Returning owners jump ahead, so stepping away never costs you your place." },
  { tag: "Students", title: "Back at 3pm.", scene: "away", to: "user",
    text: "Tell it when you will be back. Your session parks now and is restored just before you return, and three snoozes a day cover the times you just need a minute." },
  { tag: "Admins", title: "Deadline mode and dry runs.", scene: "deadline", to: "admin",
    text: "Tighten the idle window for exam week with one switch, or run in dry-run mode first and get a report of what parking would have reclaimed." },
  { tag: "Impact", title: "Receipts and a leaderboard.", scene: "impact", to: "leaderboard",
    text: "Every reclaimed slot writes a receipt: who got the GPU, whose idle session freed it, and how much waiting it saved. The leaderboard ranks people by GPU time given back." },
];

// Section 8: feature carousel with a centred card and neighbours peeking in from both sides.
export default function Carousel() {
  const [i, setI] = useState(0);
  const [paused, setPaused] = useState(false);
  const [ref, seen] = useReveal();
  const n = ITEMS.length;
  const go = (k) => setI(((k % n) + n) % n);

  useEffect(() => {
    if (paused) return;
    const t = setInterval(() => setI((x) => (x + 1) % n), 5500);
    return () => clearInterval(t);
  }, [paused, n]);

  return (
    <section className={`carousel reveal ${seen ? "in" : ""}`} ref={ref}
             onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}>
      <h2 className="carousel-title">Five things Baton<br />does for a lab.</h2>
      <div className="car-track">
        {ITEMS.map((it, k) => {
          let off = k - i;
          if (off > n / 2) off -= n;
          if (off < -n / 2) off += n;
          const cls = off === 0 ? "center" : Math.abs(off) === 1 ? "side" : "hidden";
          return (
            <article key={it.tag} className={`car-card ${cls}`} style={{ "--off": off }}
                     onClick={() => off !== 0 && go(k)} aria-hidden={off !== 0}>
              <div className="eyebrow"><span className="dot" />{it.tag}</div>
              <div className="car-image">
                <Scene kind={it.scene} active={off === 0} />
                <span className="car-label">{it.title}</span>
              </div>
              <p>{it.text} {off === 0 && <a href={href(it.to)} className="car-link">Try it →</a>}</p>
            </article>
          );
        })}
      </div>
      <div className="car-controls">
        <button className="icon-btn" onClick={() => go(i - 1)} aria-label="Previous">
          <svg viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6" /></svg>
        </button>
        {ITEMS.map((it, k) => (
          <button key={it.tag} className={`car-dot ${k === i ? "on" : ""}`} onClick={() => go(k)} aria-label={it.tag} />
        ))}
        <button className="icon-btn" onClick={() => go(i + 1)} aria-label="Next">
          <svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6" /></svg>
        </button>
      </div>
    </section>
  );
}
