import { DragEvent, useEffect, useRef, useState } from "react";
import { Alert } from "antd";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
import dayjs from "dayjs";
import "./chat.css";
import Composer, { Attachment } from "./Composer";
import ProcessTrace, { TOOL_LABEL } from "./ProcessTrace";
import { chatError, ChatResult, HistoryItem, streamQuestion, TraceStep } from "../../api/chat";
import { Batch, errorMessage, FILE_TYPE_LABEL, FileType, RangeType, uploadFile } from "../../api/uploads";

type AssistantMsg = { id: number; role: "assistant"; text: string; steps: TraceStep[]; running: string | null; result?: ChatResult };
type Msg =
  | { id: number; role: "user"; text: string }
  | AssistantMsg
  | { id: number; role: "import"; batch: Batch }
  | { id: number; role: "status"; text: string }
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

export default function ChatPage() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileType, setFileType] = useState<FileType>("order");
  const [rangeType, setRangeType] = useState<RangeType>("full_day");
  const [bizDate, setBizDate] = useState<dayjs.Dayjs | null>(dayjs().subtract(1, "day"));
  const [period, setPeriod] = useState<[dayjs.Dayjs, dayjs.Dayjs] | null>(null);
  const [dragging, setDragging] = useState(false);
  const [showJump, setShowJump] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const dragDepth = useRef(0);

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

  const buildAttachment = (): Attachment | undefined => {
    if (!file) return undefined;
    if (fileType === "order") return { file, fields: { file_type: "order" } };
    return {
      file,
      fields: {
        file_type: fileType,
        range_type: rangeType,
        biz_date: bizDate?.format("YYYY-MM-DD"),
        period_start: rangeType === "period" ? period?.[0].format("HH:mm") : undefined,
        period_end: rangeType === "period" ? period?.[1].format("HH:mm") : undefined,
      },
    };
  };

  const handleSubmit = async (payload: { text: string; attachment?: Attachment }) => {
    setBusy(true);
    const history = historyFrom(messages);
    try {
      if (payload.attachment) {
        const { file: f, fields } = payload.attachment;
        append({ id: nextId(), role: "user", text: `上传${FILE_TYPE_LABEL[fields.file_type]}：${f.name}` });
        const pending = nextId();
        append({ id: pending, role: "status", text: "正在导入，解析并校验字段…" });
        try {
          const batch = await uploadFile(f, fields);
          replace(pending, { id: pending, role: "import", batch });
        } catch (err) {
          replace(pending, { id: pending, role: "error", text: errorMessage(err) });
        }
      }
      if (payload.text) {
        append({ id: nextId(), role: "user", text: payload.text });
        const pending = nextId();
        append({ id: pending, role: "assistant", text: "", steps: [], running: null });
        try {
          await streamQuestion(payload.text, history, {
            onToken: (t) => patch(pending, (m) => ({ ...m, text: m.text + t })),
            onToolStart: (name) => patch(pending, (m) => ({ ...m, text: "", running: TOOL_LABEL[name] ?? name })),
            onStep: (step) =>
              patch(pending, (m) => ({ ...m, steps: [...m.steps, step], running: step.type === "tool" ? null : m.running })),
            onDone: (result) => patch(pending, (m) => ({ ...m, result, text: "", running: null, steps: result.trace })),
          });
        } catch (err) {
          replace(pending, { id: pending, role: "error", text: chatError(err) });
        }
      }
    } finally {
      setBusy(false);
    }
  };

  const send = () => {
    const t = text.trim();
    const attachment = buildAttachment();
    if (!t && !attachment) return;
    setText("");
    setFile(null);
    handleSubmit({ text: t, attachment });
  };

  const ask = (q: string) => !busy && handleSubmit({ text: q });

  // 整页拖放 .xlsx
  const onDragEnter = (e: DragEvent) => {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    dragDepth.current += 1;
    setDragging(true);
  };
  const onDragLeave = () => {
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragging(false);
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    const f = e.dataTransfer.files[0];
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".xlsx")) {
      append({ id: nextId(), role: "error", text: "只支持 .xlsx 格式的 Excel 文件" });
      return;
    }
    setFile(f);
  };

  return (
    <div className="chat-page" onDragEnter={onDragEnter} onDragOver={(e) => e.preventDefault()} onDragLeave={onDragLeave} onDrop={onDrop}>
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
              <kbd>Enter</kbd> 发送 · <kbd>/</kbd> 聚焦输入 · 把 .xlsx 拖进页面即可导入
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

      <Composer
        busy={busy}
        file={file}
        onFile={setFile}
        fileType={fileType}
        onFileType={setFileType}
        rangeType={rangeType}
        onRangeType={setRangeType}
        bizDate={bizDate}
        onBizDate={setBizDate}
        period={period}
        onPeriod={setPeriod}
        text={text}
        onText={setText}
        onSend={send}
      />

      {dragging && (
        <div className="drop-veil">
          <div className="drop-frame">
            <h3>松开，导入这份 Excel</h3>
            <p>订单表 · 查询量表 · 数据质量表 — 导入前可选择类型与日期</p>
          </div>
        </div>
      )}
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
  if (msg.role === "status") {
    return <div className="status-line">◌ {msg.text}</div>;
  }
  if (msg.role === "error") {
    return <Alert type="error" showIcon message={msg.text} style={{ borderRadius: 12 }} />;
  }
  if (msg.role === "import") {
    const b = msg.batch;
    const range = b.file_type === "order" ? "按表内支付时间入库" : `${b.biz_date} ${b.range_type === "full_day" ? "全天" : `${b.period_start}–${b.period_end}`}`;
    const stats = Object.entries(b.summary ?? {}).filter(([, v]) => typeof v !== "object");
    return (
      <div className="ticket">
        <div className="ticket-title">已入账 · {FILE_TYPE_LABEL[b.file_type]}</div>
        <div className="ticket-meta">
          批次 #{b.id} · {b.file_name}
          <br />
          {range}
        </div>
        <div className="ticket-stat">
          <div>
            <b>{b.row_count}</b>记录
          </div>
          {stats.slice(0, 3).map(([k, v]) => (
            <div key={k}>
              <b>{String(v)}</b>
              {k}
            </div>
          ))}
        </div>
      </div>
    );
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

  if (!answer && !msg.running && msg.steps.length === 0) {
    return (
      <div className="report">
        <div className="thinking">
          <i /><i /><i /> 正在理解问题
        </div>
      </div>
    );
  }

  return (
    <div className="report">
      {answer && (
        <div className={`report-body ${streaming ? "caret" : ""}`}>
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
            {answer}
          </ReactMarkdown>
        </div>
      )}
      {!answer && msg.running && (
        <div className="status-line">
          <span className="thinking"><i /><i /><i /></span> 正在计算
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
