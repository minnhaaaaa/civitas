import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          graph: ["@xyflow/react"],
          motion: ["@gsap/react", "gsap"],
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8001",
      "/health": "http://127.0.0.1:8001",
    },
  },
});
