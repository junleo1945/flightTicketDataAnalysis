import { useEffect, useState, type DragEvent, type FormEvent } from "react";
import { FileSpreadsheet, Loader2, Upload as UploadIcon } from "lucide-react";
import { toast } from "sonner";
import { Batch, FILE_TYPE_LABEL, FileType, RangeType, errorMessage, listBatches, uploadFile } from "../../api/uploads";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { DatePicker } from "@/components/ui/date-picker";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import BatchRowsDialog from "./BatchRowsDialog";

const PAGE_SIZE = 10;

const FILE_TYPE_OPTIONS: { value: FileType; label: string }[] = [
  { value: "order", label: "订单表（OrderList，时间由表内支付时间决定）" },
  { value: "query", label: "查询量表" },
  { value: "quality", label: "数据质量表" },
];

interface FormState {
  file_type: FileType;
  range_type: RangeType;
  biz_date: string; // YYYY-MM-DD
  period_start: string; // HH:mm
  period_end: string;
}

const INITIAL: FormState = { file_type: "order", range_type: "full_day", biz_date: "", period_start: "", period_end: "" };

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
    </div>
  );
}

export default function UploadPage() {
  const [form, setForm] = useState<FormState>(INITIAL);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [page, setPage] = useState(1);
  const [detailBatch, setDetailBatch] = useState<Batch | null>(null);

  const needsWindow = form.file_type === "query" || form.file_type === "quality";

  const reload = () => listBatches().then(setBatches).catch(() => setBatches([]));
  useEffect(() => {
    reload();
  }, []);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((f) => ({ ...f, [key]: value }));

  const pickFile = (f: File | undefined) => {
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".xlsx")) {
      toast.error("只支持 .xlsx 格式的 Excel 文件");
      return;
    }
    setFile(f);
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    pickFile(e.dataTransfer.files[0]);
  };

  const validate = (): string | null => {
    if (!file) return "请选择要上传的 Excel 文件";
    if (!needsWindow) return null;
    if (!form.biz_date) return "请选择业务日期";
    if (form.range_type === "period" && (!form.period_start || !form.period_end)) return "请选择时间段";
    return null;
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const problem = validate();
    if (problem) {
      toast.error(problem);
      return;
    }
    setSubmitting(true);
    try {
      const fields = needsWindow
        ? {
            file_type: form.file_type,
            range_type: form.range_type,
            biz_date: form.biz_date,
            period_start: form.range_type === "period" ? form.period_start : undefined,
            period_end: form.range_type === "period" ? form.period_end : undefined,
          }
        : { file_type: form.file_type };
      const batch = await uploadFile(file as File, fields);
      toast.success(`${FILE_TYPE_LABEL[batch.file_type]}导入成功，共 ${batch.row_count} 条`);
      setFile(null);
      reload();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  const pageCount = Math.max(1, Math.ceil(batches.length / PAGE_SIZE));
  const pageRows = batches.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>数据上传</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="flex flex-col gap-5">
            <Field label="文件类型">
              <Select value={form.file_type} onValueChange={(v) => set("file_type", v as FileType)}>
                <SelectTrigger className="max-w-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FILE_TYPE_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            {needsWindow && (
              <>
                <Field label="数据范围">
                  <RadioGroup value={form.range_type} onValueChange={(v) => set("range_type", v as RangeType)}>
                    <label className="flex items-center gap-2 text-sm">
                      <RadioGroupItem value="full_day" />
                      完整一天
                    </label>
                    <label className="flex items-center gap-2 text-sm">
                      <RadioGroupItem value="period" />
                      某个时间段
                    </label>
                  </RadioGroup>
                </Field>
                <Field label="业务日期">
                  <DatePicker value={form.biz_date} onChange={(v) => set("biz_date", v)} />
                </Field>
                {form.range_type === "period" && (
                  <Field label="时间段（北京时间）">
                    <div className="flex items-center gap-2">
                      <Input type="time" className="w-32" value={form.period_start} onChange={(e) => set("period_start", e.target.value)} />
                      <span className="text-muted-foreground">至</span>
                      <Input type="time" className="w-32" value={form.period_end} onChange={(e) => set("period_end", e.target.value)} />
                    </div>
                  </Field>
                )}
              </>
            )}

            <Field label="Excel 文件" hint="原始文件不会保存，只保存解析后的数据（乘机人姓名等敏感字段不入库）">
              <label
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
                className={cn(
                  "flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed border-border bg-card/60 px-6 py-10 text-center transition-colors hover:bg-muted/60",
                  dragging && "border-primary bg-accent",
                )}
              >
                {file ? <FileSpreadsheet className="size-8 text-primary" /> : <UploadIcon className="size-8 text-muted-foreground" />}
                <span className="text-sm">{file ? file.name : "点击或拖拽 .xlsx 文件到此处"}</span>
                <input type="file" accept=".xlsx" className="hidden" onChange={(e) => pickFile(e.target.files?.[0])} />
              </label>
            </Field>

            <div>
              <Button type="submit" disabled={submitting}>
                {submitting && <Loader2 className="animate-spin" />}
                开始导入
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>导入记录</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead className="bg-muted/60 text-left text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium">批次</th>
                  <th className="px-3 py-2 font-medium">类型</th>
                  <th className="px-3 py-2 font-medium">文件名</th>
                  <th className="px-3 py-2 font-medium">数据范围</th>
                  <th className="px-3 py-2 text-right font-medium">记录数</th>
                  <th className="px-3 py-2 font-medium">上传时间</th>
                  <th className="px-3 py-2 text-right font-medium">明细</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.length === 0 && (
                  <tr>
                    <td colSpan={7} className="px-3 py-8 text-center text-muted-foreground">
                      还没有导入记录
                    </td>
                  </tr>
                )}
                {pageRows.map((b) => (
                  <tr key={b.id} className="border-t border-border">
                    <td className="px-3 py-2">{b.id}</td>
                    <td className="px-3 py-2">{FILE_TYPE_LABEL[b.file_type]}</td>
                    <td className="max-w-56 truncate px-3 py-2" title={b.file_name}>
                      {b.file_name}
                    </td>
                    <td className="px-3 py-2">
                      {b.file_type === "order"
                        ? `${b.data_min_time ?? "-"} ~ ${b.data_max_time ?? "-"}`
                        : `${b.biz_date} ${b.range_type === "full_day" ? "全天" : `${b.period_start}-${b.period_end}`}`}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{b.row_count}</td>
                    <td className="px-3 py-2">{b.uploaded_at ? new Date(b.uploaded_at).toLocaleString("zh-CN") : "-"}</td>
                    <td className="px-3 py-2 text-right">
                      <Button variant="outline" size="sm" onClick={() => setDetailBatch(b)}>
                        查看
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {batches.length > PAGE_SIZE && (
            <div className="flex items-center justify-end gap-3 text-sm">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                上一页
              </Button>
              <span className="text-muted-foreground">
                第 {page} / {pageCount} 页
              </span>
              <Button variant="outline" size="sm" disabled={page >= pageCount} onClick={() => setPage((p) => p + 1)}>
                下一页
              </Button>
            </div>
          )}
          <CardDescription>
            同一文件（内容相同）不会重复导入。查询量、数据质量表同一日期与范围的记录会被覆盖更新。
          </CardDescription>
        </CardContent>
      </Card>
      <BatchRowsDialog batch={detailBatch} onClose={() => setDetailBatch(null)} />
    </div>
  );
}
