import axios from "axios";

// 统一的 API 客户端，baseURL 为相对路径 /api，由 nginx（生产）或 vite（开发）转发到后端
export const api = axios.create({
  baseURL: "/api",
  timeout: 30000,
});
