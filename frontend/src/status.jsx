import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "./api";

// Live status from /api/stream (server-sent events), with a polling fallback.
const StatusContext = createContext(null);
const MAX_EVENTS = 300;

export function StatusProvider({ children }) {
  const [status, setStatus] = useState(null);
  const [events, setEvents] = useState([]);        // newest first
  const [connected, setConnected] = useState(false);
  const [now, setNow] = useState(Date.now() / 1000);
  const listeners = useRef(new Set());

  useEffect(() => {
    api.status().then(setStatus).catch(() => {});
    api.events(100).then(setEvents).catch(() => {});

    const es = new EventSource("/api/stream");
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.addEventListener("status", (e) => setStatus(JSON.parse(e.data)));
    es.addEventListener("events", (e) => {
      const fresh = JSON.parse(e.data);           // oldest first
      fresh.forEach((ev) => listeners.current.forEach((fn) => fn(ev)));
      setEvents((prev) => {
        const seen = new Set(prev.map((x) => x.id));
        const add = fresh.filter((x) => !seen.has(x.id)).reverse();
        return [...add, ...prev].slice(0, MAX_EVENTS);
      });
    });
    const tick = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => { es.close(); clearInterval(tick); };
  }, []);

  // The stream sends no `online` flag; a snapshot older than 5 s means the daemon stopped.
  const online = !!status?.time && now - status.time < 5;
  // Stable identities: Toasts subscribes in an effect keyed on onEvent, and `now` re-renders every second.
  const refresh = useCallback(() => api.status().then(setStatus).catch(() => {}), []);
  const onEvent = useCallback((fn) => { listeners.current.add(fn); return () => listeners.current.delete(fn); }, []);

  return (
    <StatusContext.Provider value={{ status, events, connected, online, now, refresh, onEvent }}>
      {children}
    </StatusContext.Provider>
  );
}

export const useStatus = () => useContext(StatusContext);
