import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, Vite serves the SPA on :5173 and proxies backend paths to Flask on
// :5000 (auth cookie + audio streaming + fonts). In prod, Flask serves
// frontend/dist directly, so these proxies are dev-only.
const FLASK = "http://127.0.0.1:5000";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: FLASK, changeOrigin: true },
      "/audio": { target: FLASK, changeOrigin: true },
      "/static": { target: FLASK, changeOrigin: true },
      "/healthz": { target: FLASK, changeOrigin: true },
    },
  },
});
