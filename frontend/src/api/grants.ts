import { api } from "./client";

export interface GrantUser {
  user_id: number;
  username: string;
  display_name: string;
}

export interface GrantList {
  granted_to_me: GrantUser[]; // 别人授权给我的数据
  granted_by_me: GrantUser[]; // 我授权给了谁
}

export async function listGrants(): Promise<GrantList> {
  const { data } = await api.get<GrantList>("/data-grants");
  return data;
}

export async function addGrant(username: string): Promise<GrantUser> {
  const { data } = await api.post<GrantUser>("/data-grants", { username });
  return data;
}

export async function revokeGrant(userId: number): Promise<void> {
  await api.delete(`/data-grants/${userId}`);
}

// 把接口返回的错误信息转成可读文本（后端 HTTPException 的 detail 为中文提示）
export function grantErrorMessage(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return "操作失败，请稍后重试";
}
