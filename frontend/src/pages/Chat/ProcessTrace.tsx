import { useState } from "react";
import type { TraceStep, ToolStep } from "../../api/chat";

export const TOOL_LABEL: Record<string, string> = {
  get_data_coverage: "数据覆盖查询",
  order_volume_anomaly: "订单量异常分析",
  l2b_anomaly: "查订比异常分析",
  l2b_summary: "查订比汇总",
  profit_summary: "利润汇总",
  profit_anomaly: "利润异常分析",
  profit_orders: "利润订单明细",
  commission_rules: "佣金规则查询",
};

const ARG_LABEL: Record<string, string> = {
  date: "日期",
  x: "历史天数",
  dimension: "维度",
  channel_code: "渠道",
  supplier_code: "供应商",
  airline_code: "航司",
  route: "航线",
  window_end: "截止",
  threshold: "阈值",
  top_n: "条数",
  partner_code: "合作方",
  range_type: "范围",
  period_start: "开始",
  period_end: "结束",
  date_from: "从",
  date_to: "至",
  group_by: "分组",
  sort_by: "排序",
  only_loss: "仅亏损",
};

function argsSummary(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => `${ARG_LABEL[k] ?? k} ${String(v)}`)
    .join(" · ");
}

function ToolNode({ step }: { step: ToolStep }) {
  const [open, setOpen] = useState(false);
  const cls = step.error ? "error" : "tool";
  return (
    <div className={`tl-item ${cls}`}>
      <span className="tl-dot" />
      <div className="tl-row" onClick={() => setOpen((o) => !o)} role="button" tabIndex={0}>
        <span className="tl-title">{TOOL_LABEL[step.name] ?? step.name}</span>
        <span className="tl-args">{argsSummary(step.arguments)}</span>
        <span className="tl-ms">{step.latency_ms} ms {open ? "▴" : "▾"}</span>
      </div>
      {open && (
        <div className="tl-detail">
          <div>
            <div className="label">参数</div>
            <pre>{JSON.stringify(step.arguments, null, 2)}</pre>
          </div>
          <div>
            <div className="label">计算结果</div>
            {step.error ? <div className="tl-err">{step.error}</div> : <pre>{JSON.stringify(step.result, null, 2)}</pre>}
          </div>
        </div>
      )}
    </div>
  );
}

interface Props {
  trace: TraceStep[];
  running?: string | null;
  model?: string;
  totalMs?: number;
  defaultOpen?: boolean;
}

// 计算过程时间线：默认折叠，展开后逐步显示模型轮次与每次计算函数的参数、结果
export default function ProcessTrace({ trace, running, model, totalMs, defaultOpen = false }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  const toolCount = trace.filter((s) => s.type === "tool").length + (running ? 1 : 0);
  if (trace.length === 0 && !running) return null;

  const stats = [model, totalMs !== undefined ? `${totalMs} ms` : null].filter(Boolean).join(" · ");

  return (
    <div className="trace">
      <button className={`trace-head ${open ? "open" : ""}`} onClick={() => setOpen((o) => !o)}>
        <span className="trace-chevron">▸</span>
        <span>计算过程</span>
        <span>· {toolCount} 次计算</span>
        {running ? <span style={{ color: "var(--amber)" }}>· 执行中</span> : <span className="trace-stats">{stats}</span>}
      </button>
      {open && (
        <div className="trace-body">
          <div className="tl">
            {trace.map((step, i) =>
              step.type === "llm" ? (
                <div key={i} className="tl-item llm">
                  <span className="tl-dot" />
                  <div className="tl-note">
                    第 {step.round} 轮 · 模型决定调用 {step.tool_calls} 个计算函数 · {step.latency_ms} ms
                  </div>
                </div>
              ) : (
                <ToolNode key={i} step={step} />
              ),
            )}
            {running && (
              <div className="tl-item running">
                <span className="tl-dot" />
                <div className="tl-row">
                  <span className="tl-title">{running}</span>
                  <span className="tl-args">执行中…</span>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
