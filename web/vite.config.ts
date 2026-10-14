import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const api = process.env.RUN_COMFY_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../run_comfy/web_dist", emptyOutDir: true },
  server: { proxy: { "/api": api, "/files": api } },
});
