import { useEffect, useState } from "react";

// Minimal hash router: "#/user/alice" -> ["user", "alice"].
export function useRoute() {
  const parse = () => window.location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  const [route, setRoute] = useState(parse);
  useEffect(() => {
    const on = () => setRoute(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export const go = (...parts) => { window.location.hash = "/" + parts.map(encodeURIComponent).join("/"); };
export const href = (...parts) => "#/" + parts.map(encodeURIComponent).join("/");
