import { redirectToLogin } from "./client";

export interface LlmStep {
  type: "llm";
  round: number;
  latency_ms: number;
  tool_calls: number;
}

export interface ToolStep {
  type: "tool";
  name: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown>;
  error: string | null;
  latency_ms: number;
}

export type TraceStep = LlmStep | ToolStep;

export interface ChatResult {
  answer: string;
  trace: TraceStep[];
  model: string;
  latency_ms: number;
}

export interface HistoryItem {
  role: "user" | "assistant";
  content: string;
}

// 把接口返回的错误信息转成可读文本
export function chatError(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return "请求失败，请稍后重试";
}

export interface StreamHandlers {
  // 本次问答所属的会话 ID（新建会话时由服务端分配）
  onConversation: (id: number) => void;
  onToken: (text: string) => void;
  onToolStart: (name: string, args: Record<string, unknown>) => void;
  // 回答结构不合格，模型正在重新生成：清空已输出的文字
  onRetry: () => void;
  onStep: (step: TraceStep) => void;
  onDone: (result: ChatResult) => void;
}

// 通过 fetch 读取 SSE（EventSource 不支持 POST）。流中出现 error 事件时抛出异常。
export async function streamQuestion(
  question: string,
  history: HistoryItem[],
  conversationId: number | null,
  h: StreamHandlers,
): Promise<void> {
  const resp = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, history, conversation_id: conversationId }),
  });
  if (resp.status === 401) {
    redirectToLogin();
    throw new Error("登录已失效，请重新登录");
  }
  if (!resp.ok || !resp.body) {
    let detail = `请求失败（${resp.status}）`;
    try {
      const j = await resp.json();
      if (typeof j.detail === "string") detail = j.detail;
    } catch {
      /* 非 JSON 响应，保留默认信息 */
    }
    throw new Error(detail);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const raw = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = raw.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const ev = JSON.parse(line.slice(6));
      switch (ev.type) {
        case "conversation":
          h.onConversation(ev.id);
          break;
        case "token":
          h.onToken(ev.text);
          break;
        case "tool_start":
          h.onToolStart(ev.name, ev.arguments);
          break;
        case "retry":
          h.onRetry();
          break;
        case "step":
          h.onStep(ev.step);
          break;
        case "done":
          h.onDone({ answer: ev.answer, trace: ev.trace, model: ev.model, latency_ms: ev.latency_ms });
          break;
        case "error":
          throw new Error(ev.message);
      }
    }
  }
}
