import { api } from "./client";

export type FileType = "order" | "query" | "quality";

export const FILE_TYPE_LABEL: Record<FileType, string> = {
  order: "订单表",
  query: "查询量表",
  quality: "数据质量表",
};
export type RangeType = "full_day" | "period";

export interface Batch {
  id: number;
  file_type: FileType;
  file_name: string;
  range_type: RangeType | null;
  biz_date: string | null;
  period_start: string | null;
  period_end: string | null;
  data_min_time: string | null;
  data_max_time: string | null;
  row_count: number;
  summary: Record<string, unknown> | null;
  uploaded_at: string | null;
}

export interface UploadFields {
  file_type: FileType;
  range_type?: RangeType;
  biz_date?: string;
  period_start?: string;
  period_end?: string;
}

export async function listBatches(): Promise<Batch[]> {
  const { data } = await api.get<Batch[]>("/uploads");
  return data;
}

export async function uploadFile(file: File, fields: UploadFields): Promise<Batch> {
  const form = new FormData();
  form.append("file", file);
  Object.entries(fields).forEach(([k, v]) => {
    if (v !== undefined && v !== "") form.append(k, v);
  });
  // 订单表较大（万行级，后端解析+入库约 40 秒），沿用全局 30 秒超时会误报失败，这里与 nginx 的 300 秒读超时保持一致
  const { data } = await api.post<Batch>("/uploads", form, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 300000,
  });
  return data;
}

// 把接口返回的错误信息转成可读文本
export function errorMessage(err: unknown): string {
  const e = err as { code?: string; response?: { data?: { detail?: unknown } } };
  const detail = e?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (e?.code === "ECONNABORTED") return "等待超时，数据可能已导入，请到导入记录中确认后再重新上传";
  return "上传失败，请稍后重试";
}

export interface BatchRows {
  batch_id: number;
  file_type: FileType;
  total: number;
  page: number;
  page_size: number;
  columns: string[];
  rows: Record<string, unknown>[];
}

// 批次明细（分页）：拥有者或被授权人可查看，分析与问答不受此影响
export async function getBatchRows(batchId: number, page: number): Promise<BatchRows> {
  const { data } = await api.get<BatchRows>(`/uploads/${batchId}/rows`, { params: { page } });
  return data;
}
