import { useEffect, useState } from "react";

// Loads `fn()` whenever deps change; reloads every `pollMs` if given.
export default function useFetch(fn, deps, pollMs) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    let live = true;
    const load = () => fn().then((d) => { if (live) { setData(d); setError(null); } })
      .catch((e) => live && setError(e.message));
    load();
    const t = pollMs ? setInterval(load, pollMs) : null;
    return () => { live = false; if (t) clearInterval(t); };
  }, [...deps, n]);
  return { data, error, reload: () => setN((x) => x + 1) };
}
