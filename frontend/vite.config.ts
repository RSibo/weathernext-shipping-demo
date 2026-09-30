/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  // Tailwind v4's own plugin: design tokens live in src/index.css's @theme block.
  plugins: [react(), tailwindcss()],
  server: {
    port: 3000,
    proxy: {
      // Same-origin /api in dev and prod (FastAPI serves dist/ in prod), so no CORS anywhere.
      "/api": { target: process.env.API_TARGET ?? "http://localhost:8000", changeOrigin: true },
    },
  },
  build: { chunkSizeWarningLimit: 2000 }, // deck.gl + luma are one large, cacheable chunk
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
