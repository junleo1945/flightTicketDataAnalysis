import { api } from "./client";
import type { ChatResult, TraceStep } from "./chat";

export interface ConversationSummary {
  id: number;
  title: string;
  pinned: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface StoredMessage {
  id: number;
  question: string;
  answer: string | null;
  model: string;
  latency_ms: number;
  trace: TraceStep[] | null;
  created_at: string | null;
}

export interface ConversationDetail extends ConversationSummary {
  messages: StoredMessage[];
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const { data } = await api.get<ConversationSummary[]>("/chat/conversations");
  return data;
}

export async function getConversation(id: number): Promise<ConversationDetail> {
  const { data } = await api.get<ConversationDetail>(`/chat/conversations/${id}`);
  return data;
}

export async function updateConversation(id: number, patch: { title?: string; pinned?: boolean }): Promise<ConversationSummary> {
  const { data } = await api.patch<ConversationSummary>(`/chat/conversations/${id}`, patch);
  return data;
}

export async function deleteConversation(id: number): Promise<void> {
  await api.delete(`/chat/conversations/${id}`);
}

// 把已保存的一轮问答还原成前端可展示的回答结构
export function toChatResult(m: StoredMessage): ChatResult {
  return {
    answer: m.answer ?? "",
    trace: m.trace ?? [],
    model: m.model,
    latency_ms: m.latency_ms,
  };
}
