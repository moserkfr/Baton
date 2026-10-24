import { useState } from "react";
import { api } from "../api";
import Action from "./Action";

// "Back at 3pm": park now, restore just before the return. Either minutes or a clock time.
export default function AwayForm({ session }) {
  const [mode, setMode] = useState("minutes");
  const [minutes, setMinutes] = useState(45);
  const [time, setTime] = useState("15:00");

  function body() {
    if (mode === "minutes") return { minutes: Number(minutes) };
    const [h, m] = time.split(":").map(Number);
    const d = new Date();
    d.setHours(h, m, 0, 0);
    if (d < new Date()) d.setDate(d.getDate() + 1);
    return { until: d.getTime() / 1000 };
  }

  return (
    <div className="away-form" onClick={(e) => e.stopPropagation()}>
      <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Away for or until">
        <option value="minutes">Away for</option>
        <option value="until">Back at</option>
      </select>
      {mode === "minutes"
        ? <><input type="number" min="1" value={minutes} onChange={(e) => setMinutes(e.target.value)} style={{ width: 72 }} aria-label="Minutes" /><span className="muted">min</span></>
        : <input type="time" value={time} onChange={(e) => setTime(e.target.value)} aria-label="Return time" />}
      <Action run={() => api.away(session, body())} title="Park now and restore just before you return">Step away</Action>
    </div>
  );
}
