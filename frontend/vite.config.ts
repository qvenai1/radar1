import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base "./" so the build works when FastAPI serves it from any mount point.
// Dev server proxies API + WS to the running backend so `npm run dev`
// works against real data without CORS ceremony.
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    // host:true → bind 0.0.0.0 so `npm run dev` is reachable from other
    // LAN devices (the production FastAPI server already binds 0.0.0.0).
    // The proxy still forwards to the backend on this same machine.
    host: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        ws: true,
      },
    },
  },
  preview: {
    host: true,
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
