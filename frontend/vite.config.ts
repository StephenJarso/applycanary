import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The SPA is served from /ui by FastAPI, so asset URLs must be absolute
// against that prefix or a deep link like /ui/job/42 would resolve them
// relative to the wrong path.
export default defineConfig({
  plugins: [react()],
  base: "/ui/",
  build: {
    // Straight into the package so the built app ships with the Python code
    // and no separate static host is needed.
    outDir: "../app/web/dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    // `npm run dev` talks to the real backend on :8000 instead of a mock,
    // which keeps the dev and production data shapes identical.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
