import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, forward API and WebSocket calls to the backend on :8000
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
