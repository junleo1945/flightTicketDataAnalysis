import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发时把 /api 转发到本地后端；容器内由 nginx 转发
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
