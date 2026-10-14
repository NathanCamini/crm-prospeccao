import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Em desenvolvimento, /api vai para a API do CRM local. Em produção, o nginx faz o mesmo.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.CRM_API ?? "http://localhost:8000", changeOrigin: false } },
  },
});
