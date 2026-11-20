import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 開発時は API(uvicorn :8000)へ中継する
export default defineConfig({
  plugins: [react()],
  // maplibre-gl が大きいため(gzip で約 370 kB)
  build: { chunkSizeWarningLimit: 1600 },
  server: {
    proxy: {
      "/api": { target: "http://localhost:8000", ws: true },
    },
  },
});
