import { api } from "./client";

export interface ReadyStatus {
  status: string;
}

export async function fetchReady(): Promise<ReadyStatus> {
  const { data } = await api.get<ReadyStatus>("/ready");
  return data;
}
