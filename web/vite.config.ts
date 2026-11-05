import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // `npm run dev` proxies API calls to the Python server (python -m jobagent web).
  server: { proxy: { "/api": "http://127.0.0.1:8600" } },
});
