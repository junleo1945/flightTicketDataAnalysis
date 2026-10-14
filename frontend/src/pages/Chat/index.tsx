import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Alert } from "@/components/ui/alert";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
import dayjs from "dayjs";
import { useNavigate, useParams } from "react-router-dom";
import "./chat.css";
import Composer from "./Composer";
import ConversationList from "./ConversationList";
import ProcessTrace, { TOOL_LABEL } from "./ProcessTrace";
import { chatError, ChatResult, HistoryItem, streamQuestion, TraceStep } from "../../api/chat";
import { normalizeTables } from "../../lib/markdown";
import {
  ConversationSummary,
  deleteConversation,
  getConversation,
  listConversations,
  StoredMessage,
  toChatResult,
  updateConversation,
} from "../../api/conversations";

type AssistantMsg = { id: number; role: "assistant"; text: string; steps: TraceStep[]; running: string | null; result?: ChatResult };
type Msg =
  | { id: number; role: "user"; text: string }
  | AssistantMsg
  | { id: number; role: "error"; text: string };

const SUGGESTIONS = [
  "2026-10-11 的订单量相比前 7 天怎么样？",
  "2026-10-10 各渠道的利润分别是多少？CNY 和 USD 都要。",
  "哪些航司的利润为负？",
  "数据覆盖到哪一天？",
];

let seq = 0;
const nextId = () => ++seq;

// 表格单元格：负数标红，纯数字右对齐
const cellText = (children: unknown): string => {
  if (typeof children === "string") return children;
  if (Array.isArray(children)) return children.map(cellText).join("");
  return "";
};
const markdownComponents: Components = {
  td: ({ node: _node, children, ...rest }) => {
    const t = cellText(children).trim();
    const cls = /^-[\d,.]+%?$/.test(t) ? "neg" : /^[+-]?[\d,.]+%?$/.test(t) ? "num" : undefined;
    return <td {...rest} className={cls}>{children}</td>;
  },
};

// 把已保存的一轮问答还原成消息列表：用户问题 + 回答
function messagesFrom(m: StoredMessage): Msg[] {
  return [
    { id: nextId(), role: "user", text: m.question },
    { id: nextId(), role: "assistant", text: "", steps: [], running: null, result: toChatResult(m) },
  ];
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const [showJump, setShowJump] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const { id: routeId } = useParams();
  const navigate = useNavigate();
  const conversationId = routeId ? Number(routeId) : null;
  // convRef：发送问题时使用的会话；loadedRef：消息列表当前对应的会话（undefined 表示尚未加载）
  const convRef = useRef<number | null>(conversationId);
  const loadedRef = useRef<number | null | undefined>(undefined);
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null);
  const loadList = useCallback(() => {
    listConversations().then(setConversations).catch(() => setConversations([]));
  }, []);

  // 新消息时，若用户本来就在底部则跟随滚动
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 240) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // "/" 快速聚焦输入框（不在输入框内时）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (e.key === "/" && tag !== "TEXTAREA" && tag !== "INPUT") {
        e.preventDefault();
        document.querySelector<HTMLTextAreaElement>(".composer-box textarea")?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    loadList();
  }, [loadList]);

  // 路由中的会话变化时加载它的问答；流式过程中新建的会话已在本地，不重复加载以免覆盖正在生成的内容
  useEffect(() => {
    convRef.current = conversationId;
    if (conversationId === loadedRef.current) return;
    loadedRef.current = conversationId;
    if (conversationId === null) {
      setMessages([]);
      return;
    }
    getConversation(conversationId)
      .then((d) => {
        if (loadedRef.current === conversationId) setMessages(d.messages.flatMap(messagesFrom));
      })
      .catch(() => {
        if (loadedRef.current === conversationId) {
          setMessages([{ id: nextId(), role: "error", text: "加载会话失败，请返回新对话重试" }]);
        }
      });
  }, [conversationId]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (el) setShowJump(el.scrollHeight - el.scrollTop - el.clientHeight > 240);
  };
  const jumpToBottom = () => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });

  const replace = (id: number, next: Msg) => setMessages((ms) => ms.map((m) => (m.id === id ? next : m)));
  const patch = (id: number, fn: (m: AssistantMsg) => AssistantMsg) =>
    setMessages((ms) => ms.map((m) => (m.id === id && m.role === "assistant" ? fn(m) : m)));
  const append = (m: Msg) => setMessages((ms) => [...ms, m]);

  // 连续对话：把之前的文字问答带给模型（工具结果不回放）
  const historyFrom = (ms: Msg[]): HistoryItem[] => {
    const items: HistoryItem[] = [];
    for (const m of ms) {
      if (m.role === "user") items.push({ role: "user", content: m.text });
      if (m.role === "assistant" && m.result) items.push({ role: "assistant", content: m.result.answer });
    }
    return items.slice(-10);
  };

  const handleSubmit = async (question: string) => {
    setBusy(true);
    const history = historyFrom(messages);
    try {
      append({ id: nextId(), role: "user", text: question });
      const pending = nextId();
      append({ id: pending, role: "assistant", text: "", steps: [], running: null });
      try {
        await streamQuestion(question, history, convRef.current, {
          onConversation: (cid) => {
            if (convRef.current === cid) return;
            convRef.current = cid;
            loadedRef.current = cid;
            navigate(`/chat/${cid}`, { replace: true });
            loadList();
          },
          onToken: (t) => patch(pending, (m) => ({ ...m, text: m.text + t })),
          onToolStart: (name) => patch(pending, (m) => ({ ...m, text: "", running: TOOL_LABEL[name] ?? name })),
          onRetry: () => patch(pending, (m) => ({ ...m, text: "" })),
          onStep: (step) =>
            patch(pending, (m) => ({ ...m, steps: [...m.steps, step], running: step.type === "tool" ? null : m.running })),
          onDone: (result) => patch(pending, (m) => ({ ...m, result, text: "", running: null, steps: result.trace })),
        });
      } catch (err) {
        replace(pending, { id: pending, role: "error", text: chatError(err) });
      }
    } finally {
      setBusy(false);
      loadList();
    }
  };

  const newChat = () => {
    if (busy) return;
    convRef.current = null;
    loadedRef.current = null;
    setMessages([]);
    navigate("/chat");
  };
  const selectConversation = (id: number) => navigate(`/chat/${id}`);
  const renameConversation = async (id: number, title: string) => {
    try {
      await updateConversation(id, { title });
      loadList();
    } catch {
      toast.error("重命名失败，请稍后重试");
    }
  };
  const togglePin = async (id: number, pinned: boolean) => {
    try {
      await updateConversation(id, { pinned });
      loadList();
    } catch {
      toast.error("操作失败，请稍后重试");
    }
  };
  const removeConversation = async (id: number) => {
    try {
      await deleteConversation(id);
      if (conversationId === id) newChat();
      loadList();
    } catch {
      toast.error("删除失败，请稍后重试");
    }
  };

  const send = () => {
    const t = text.trim();
    if (!t) return;
    setText("");
    handleSubmit(t);
  };

  const ask = (q: string) => !busy && handleSubmit(q);

  return (
    <div className="flex h-full min-h-0">
      <ConversationList
        items={conversations}
        activeId={conversationId}
        disabled={busy}
        onNew={newChat}
        onSelect={selectConversation}
        onRename={renameConversation}
        onTogglePin={togglePin}
        onDelete={removeConversation}
      />
      <div className="chat-page min-w-0 flex-1">
        <div className="chat-scroll" ref={scrollRef} onScroll={onScroll}>
          {messages.length === 0 ? (
            <div className="hero">
              <div className="hero-kicker">Ledger · 北京时间 · {dayjs().format("YYYY.MM.DD")}</div>
              <h1>
                今天的账，<em>哪里不对</em>？
              </h1>
              <p className="hero-sub">
                订单量、查订比、供应质量与利润，逐项算清楚。每个数字都来自计算函数，展开「计算过程」即可核对每一步。
              </p>
              <div className="prompt-list">
                {SUGGESTIONS.map((s, i) => (
                  <div key={s} className={`prompt-item ${busy ? "disabled" : ""}`} onClick={() => ask(s)}>
                    <span className="idx">0{i + 1}</span>
                    <span>{s}</span>
                    <span className="arrow">→</span>
                  </div>
                ))}
              </div>
              <div className="hero-hint">
                <kbd>Enter</kbd> 发送 · <kbd>/</kbd> 聚焦输入
              </div>
            </div>
          ) : (
            <div className="chat-column">
              {messages.map((m) => (
                <MessageView key={m.id} msg={m} />
              ))}
            </div>
          )}
        </div>

        {showJump && (
          <button className="to-bottom" onClick={jumpToBottom} aria-label="回到底部">
            ↓
          </button>
        )}

        <Composer busy={busy} text={text} onText={setText} onSend={send} />
      </div>
    </div>
  );
}

function MessageView({ msg }: { msg: Msg }) {
  if (msg.role === "user") {
    return (
      <div className="turn-user">
        <div className="bubble-user">{msg.text}</div>
      </div>
    );
  }
  if (msg.role === "error") {
    return <Alert variant="destructive">{msg.text}</Alert>;
  }
  return <AssistantView msg={msg} />;
}

function AssistantView({ msg }: { msg: AssistantMsg }) {
  const [copied, setCopied] = useState(false);
  const r = msg.result;
  const answer = r ? r.answer : msg.text;
  const streaming = !r;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(answer);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* 剪贴板不可用时静默忽略 */
    }
  };

  return (
    <div className="report">
      {answer && (
        <div className={`report-body ${streaming ? "caret" : ""}`}>
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
            {normalizeTables(answer)}
          </ReactMarkdown>
        </div>
      )}
      {!answer && msg.running && (
        <div className="status-line">
          <span className="thinking"><i /><i /><i /></span> 正在计算
        </div>
      )}
      {/* 等待模型输出：包括第一轮思考，以及工具算完、模型整理回答前的间隔（此时 running 已清空，过程也不会展开） */}
      {!answer && !msg.running && streaming && (
        <div className="status-line">
          <span className="thinking"><i /><i /><i /></span> {msg.steps.length === 0 ? "正在理解问题" : "正在整理回答"}
        </div>
      )}
      {r && (
        <div className="report-actions">
          <button onClick={copy}>{copied ? "已复制" : "复制回答"}</button>
        </div>
      )}
      {(msg.steps.length > 0 || msg.running) && (
        <ProcessTrace
          trace={r ? r.trace : msg.steps}
          running={msg.running}
          model={r?.model}
          totalMs={r?.latency_ms}
        />
      )}
    </div>
  );
}
