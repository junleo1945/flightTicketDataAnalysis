import { api } from "./client";

export interface Me {
  id: number;
  username: string;
  display_name: string;
}

// 当前登录用户；未登录时接口返回 401，调用方捕获后视为未登录
export async function getMe(): Promise<Me> {
  const { data } = await api.get<Me>("/user/info");
  return data;
}

export async function login(username: string, password: string): Promise<void> {
  await api.post("/user/login", { username, password });
}

export async function logout(): Promise<void> {
  await api.post("/user/logout");
}

// 把登录接口返回的错误信息转成可读文本
export function loginError(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return "登录失败，请稍后再试";
}
