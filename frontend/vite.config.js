import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies the API to the FastAPI dashboard (python -m baton.dashboard.app).
// `npm run build` writes dist/, which the dashboard serves at /app.
const backend = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 5173,
    proxy: { "/api": backend, "/files": backend },
  },
});
