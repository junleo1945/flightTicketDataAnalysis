import { useRef } from "react";
import { DatePicker, Input, TimePicker } from "antd";
import { ArrowUpOutlined, CloseOutlined, PaperClipOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import type { FileType, RangeType, UploadFields } from "../../api/uploads";

export interface Attachment {
  file: File;
  fields: UploadFields;
}

const TYPE_OPTIONS: { value: FileType; label: string }[] = [
  { value: "order", label: "订单表" },
  { value: "query", label: "查询量表" },
  { value: "quality", label: "数据质量表" },
];

interface Props {
  busy: boolean;
  file: File | null;
  onFile: (f: File | null) => void;
  fileType: FileType;
  onFileType: (t: FileType) => void;
  rangeType: RangeType;
  onRangeType: (r: RangeType) => void;
  bizDate: dayjs.Dayjs | null;
  onBizDate: (d: dayjs.Dayjs | null) => void;
  period: [dayjs.Dayjs, dayjs.Dayjs] | null;
  onPeriod: (p: [dayjs.Dayjs, dayjs.Dayjs] | null) => void;
  text: string;
  onText: (t: string) => void;
  onSend: () => void;
}

export default function Composer(p: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const needsWindow = p.fileType !== "order" && !!p.file;
  const windowReady = !needsWindow || (!!p.bizDate && (p.rangeType === "full_day" || !!p.period));
  const canSend = !p.busy && (p.text.trim().length > 0 || (!!p.file && windowReady));
  const today = dayjs();
  const presets: { label: string; date: dayjs.Dayjs }[] = [
    { label: "今天", date: today },
    { label: "昨天", date: today.subtract(1, "day") },
    { label: "前天", date: today.subtract(2, "day") },
  ];

  return (
    <div className="composer-wrap">
      <div className="composer">
        {p.file && (
          <div className="attach-ticket">
            <div className="attach-stub">
              <b>XLS</b>
              <span>{p.fileType === "order" ? "订单" : p.fileType === "query" ? "查询" : "质量"}</span>
            </div>
            <div className="attach-body">
              <div className="attach-top">
                <span className="attach-name" title={p.file.name}>{p.file.name}</span>
                <span className="attach-size">{Math.max(1, Math.round(p.file.size / 1024))} KB</span>
                <button className="attach-close" onClick={() => p.onFile(null)} aria-label="移除文件">
                  <CloseOutlined />
                </button>
              </div>
              <div className="attach-row">
                {TYPE_OPTIONS.map((o) => (
                  <button key={o.value} className={`chip ${p.fileType === o.value ? "on" : ""}`} onClick={() => p.onFileType(o.value)}>
                    {o.label}
                  </button>
                ))}
              </div>
              {p.fileType === "order" ? (
                <div className="attach-hint" style={{ color: "var(--ink-3)" }}>订单按表内支付时间自动归日，无需选择日期</div>
              ) : (
                <div className="attach-row">
                  {presets.map((pr) => (
                    <button
                      key={pr.label}
                      className={`chip ${p.bizDate?.isSame(pr.date, "day") ? "on" : ""}`}
                      onClick={() => p.onBizDate(pr.date)}
                    >
                      {pr.label}
                    </button>
                  ))}
                  <DatePicker size="small" value={p.bizDate} onChange={p.onBizDate} placeholder="其他日期" style={{ width: 130 }} />
                  <span style={{ width: 1, height: 18, background: "var(--line)" }} />
                  <button className={`chip ${p.rangeType === "full_day" ? "on" : ""}`} onClick={() => p.onRangeType("full_day")}>
                    完整一天
                  </button>
                  <button className={`chip ${p.rangeType === "period" ? "on" : ""}`} onClick={() => p.onRangeType("period")}>
                    时间段
                  </button>
                  {p.rangeType === "period" && (
                    <TimePicker.RangePicker size="small" format="HH:mm" value={p.period} onChange={(v) => p.onPeriod(v as [dayjs.Dayjs, dayjs.Dayjs] | null)} />
                  )}
                  {!windowReady && <span className="attach-hint">请选择日期和时间段</span>}
                </div>
              )}
            </div>
          </div>
        )}

        <div className="composer-box">
          <Input.TextArea
            variant="borderless"
            autoSize={{ minRows: 1, maxRows: 6 }}
            value={p.text}
            placeholder={p.file ? "补充一个问题，导入完成后自动提问（可留空）" : "问点什么，比如：2026-10-11 的订单量相比前 7 天怎么样？"}
            onChange={(e) => p.onText(e.target.value)}
            onPressEnter={(e) => {
              if (!e.shiftKey) {
                e.preventDefault();
                if (canSend) p.onSend();
              }
            }}
            style={{ padding: 0, resize: "none" }}
          />
          <div className="composer-bar">
            <div className="composer-tools">
              <button className="attach-btn" onClick={() => fileInput.current?.click()} disabled={p.busy}>
                <PaperClipOutlined /> 上传 Excel
              </button>
              <span className="kbd-hint">Enter 发送 · Shift+Enter 换行 · 也可以把 .xlsx 拖进页面</span>
            </div>
            <button className="send-btn" onClick={p.onSend} disabled={!canSend} aria-label="发送">
              <ArrowUpOutlined />
            </button>
          </div>
        </div>
        <input
          ref={fileInput}
          type="file"
          accept=".xlsx"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) p.onFile(f);
            e.target.value = "";
          }}
        />
      </div>
    </div>
  );
}
