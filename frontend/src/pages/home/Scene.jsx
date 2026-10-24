// Small animated illustrations for the carousel cards. Animations run only on the centred card.
export default function Scene({ kind, active }) {
  return (
    <svg className={`scene scene-${kind} ${active ? "active" : ""}`} viewBox="0 0 400 240" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <defs>
        <radialGradient id={`glow-${kind}`} cx="70%" cy="20%" r="90%">
          <stop offset="0" stopColor="#4b3fb0" />
          <stop offset=".55" stopColor="#1d1a3a" />
          <stop offset="1" stopColor="#0e0d18" />
        </radialGradient>
        <pattern id={`dots-${kind}`} width="12" height="12" patternUnits="userSpaceOnUse">
          <circle cx="1" cy="1" r="1" fill="#ffffff" opacity=".08" />
        </pattern>
      </defs>
      <rect width="400" height="240" fill={`url(#glow-${kind})`} />
      <rect width="400" height="240" fill={`url(#dots-${kind})`} />
      {SCENES[kind]?.()}
    </svg>
  );
}

const SCENES = {
  park: () => (
    <g>
      <text x="40" y="44" className="s-label">GPU · SLOT 1</text>
      <rect x="40" y="52" width="320" height="58" rx="6" className="s-box" />
      {Array.from({ length: 12 }, (_, i) => (
        <rect key={i} x={52 + (i % 6) * 50} y={60 + Math.floor(i / 6) * 22} width="40" height="16" rx="3"
              className="s-block move" style={{ animationDelay: `${(i % 6) * 0.08 + Math.floor(i / 6) * 0.04}s` }} />
      ))}
      <text x="40" y="160" className="s-label">HOST RAM</text>
      <rect x="40" y="168" width="320" height="44" rx="6" className="s-box dim" />
    </g>
  ),
  queue: () => (
    <g>
      <text x="40" y="60" className="s-label">QUEUE · FAIR SHARE</text>
      <rect x="290" y="86" width="74" height="74" rx="10" className="s-box" />
      <text x="327" y="180" textAnchor="middle" className="s-label">GPU</text>
      <g className="s-march">
        {[0, 1, 2, 3, 4].map((k) => (
          <g key={k} transform={`translate(${60 + k * 46}, 123)`} opacity={1 - k * 0.15}>
            <circle r="16" className={k === 0 ? "s-person first" : "s-person"} />
            <text y="40" textAnchor="middle" className="s-label">{k + 1}</text>
          </g>
        ))}
      </g>
      <text x="40" y="214" className="s-label hi">ETA ~22 S · RETURNING OWNERS FIRST</text>
    </g>
  ),
  away: () => (
    <g transform="translate(200 116)">
      <circle r="74" className="s-box" />
      <circle r="74" className="s-arc" pathLength="1" transform="rotate(-90)" />
      {Array.from({ length: 12 }, (_, i) => (
        <line key={i} y1="-64" y2={i % 3 ? "-58" : "-52"} transform={`rotate(${i * 30})`} className="s-tick" />
      ))}
      <line y2="-40" className="s-hand slow" />
      <line y2="-56" className="s-hand fast" />
      <circle r="4" fill="#fff" />
      <text y="108" textAnchor="middle" className="s-label hi">PARKED · BACK AT 15:00</text>
    </g>
  ),
  deadline: () => (
    <g>
      <text x="60" y="44" className="s-label">OCTOBER · EXAM WEEK</text>
      {Array.from({ length: 28 }, (_, i) => {
        const r = Math.floor(i / 7), c = i % 7;
        return <rect key={i} x={60 + c * 42} y={56 + r * 36} width="36" height="30" rx="4" className={r === 2 ? "s-day hot" : "s-day"} />;
      })}
      <text x="60" y="216" className="s-label hi">IDLE WINDOW 10 MIN · GRACE 2 MIN</text>
    </g>
  ),
  impact: () => (
    <g>
      <text x="40" y="44" className="s-label">GPU TIME GIVEN BACK</text>
      {[0.95, 0.72, 0.55, 0.38, 0.22].map((v, k) => (
        <g key={k} transform={`translate(40 ${62 + k * 28})`}>
          <rect width="200" height="16" rx="3" className="s-box dim" />
          <rect width={200 * v} height="16" rx="3" className="s-bar" style={{ animationDelay: `${k * 0.1}s` }} />
        </g>
      ))}
      <g transform="translate(270 56) rotate(4)">
        <rect width="96" height="132" rx="4" fill="#f7f7f3" />
        <text x="10" y="22" className="s-receipt b">RECEIPT</text>
        <text x="10" y="46" className="s-receipt">C got B's GPU</text>
        <line x1="10" x2="86" y1="58" y2="58" stroke="#ccc" strokeDasharray="3 3" />
        <text x="10" y="76" className="s-receipt">waited 33 s</text>
        <text x="10" y="94" className="s-receipt">saved ≥ 10 s</text>
        <text x="10" y="118" className="s-receipt b">THANK YOU</text>
      </g>
    </g>
  ),
};
