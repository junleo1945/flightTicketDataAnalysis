import axios from "axios";

// 统一的 API 客户端，baseURL 为相对路径 /api，由 nginx（生产）或 vite（开发）转发到后端
export const api = axios.create({
  baseURL: "/api",
  timeout: 30000,
});

// 跳转到登录页，并记住当前页面，登录后回到原处
export function redirectToLogin(): void {
  if (window.location.pathname.startsWith("/login")) return;
  const redirect = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.replace(`/login?redirect=${redirect}`);
}

// 会话失效（401）时统一跳转登录页；/user/* 自身的 401 由调用方处理（如登录密码错误）
api.interceptors.response.use(
  (res) => res,
  (err) => {
    const url: string = err?.config?.url ?? "";
    if (err?.response?.status === 401 && !url.startsWith("/user/")) redirectToLogin();
    return Promise.reject(err);
  },
);
