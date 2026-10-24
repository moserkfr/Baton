import { useEffect, useRef, useState } from "react";
import "./BottomSheet.css";

// A sheet that slides up from the bottom. Closes on backdrop click, Escape, or dragging the handle down.
export default function BottomSheet({ open, onClose, label, children }) {
  const [drag, setDrag] = useState(0);
  const start = useRef(null);
  // Keep showing the last content while the close animation runs.
  const last = useRef(null);
  if (open) last.current = children;
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = ""; };
  }, [open, onClose]);

  const down = (e) => { start.current = e.clientY; e.currentTarget.setPointerCapture(e.pointerId); };
  const move = (e) => { if (start.current != null) setDrag(Math.max(0, e.clientY - start.current)); };
  const up = () => {
    if (start.current == null) return;
    start.current = null;
    if (drag > 120) onClose();
    setDrag(0);
  };

  return (
    <div className={`sheet-root ${open ? "open" : ""}`} aria-hidden={!open}>
      <div className="sheet-backdrop" onClick={onClose} />
      <section
        className="sheet" role="dialog" aria-modal="true" aria-label={label}
        style={drag ? { transform: `translateY(${drag}px)`, transition: "none" } : undefined}
      >
        <div className="sheet-handle" onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
          <span />
        </div>
        <button className="sheet-close icon-btn" onClick={onClose} aria-label="Close">
          <svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
        <div className="sheet-body">{last.current}</div>
      </section>
    </div>
  );
}
