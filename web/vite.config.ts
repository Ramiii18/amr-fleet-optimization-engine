import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The engine's HTTP service (amr-api). The development server forwards /api to it, so the
// browser talks to one origin and the service needs no CORS configuration (D34).
const API = process.env.AMR_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: API, changeOrigin: true, rewrite: (path) => path.replace(/^\/api/, "") },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    proxy: {
      "/api": { target: API, changeOrigin: true, rewrite: (path) => path.replace(/^\/api/, "") },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
