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
  const { data } = await api.post<Batch>("/uploads", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

// 把接口返回的错误信息转成可读文本
export function errorMessage(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return "上传失败，请稍后重试";
}
