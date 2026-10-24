import { useEffect, useState } from "react";

// "light" | "dark" | "system", remembered per browser. "system" follows prefers-color-scheme (see base.css).
function load() {
  try { return localStorage.getItem("gpuq.theme") || "system"; } catch { return "system"; }
}

export function useTheme() {
  const [theme, setTheme] = useState(load);
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try { localStorage.setItem("gpuq.theme", theme); } catch { /* storage blocked */ }
  }, [theme]);

  const isDark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  return { theme, isDark, toggle: () => setTheme(isDark ? "light" : "dark") };
}
