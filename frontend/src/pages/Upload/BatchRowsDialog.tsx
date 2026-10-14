import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Batch, BatchRows, getBatchRows } from "../../api/uploads";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const PAGE_SIZE = 50;

function cell(v: unknown): string {
  return v === null || v === undefined ? "" : String(v);
}

export default function BatchRowsDialog({ batch, onClose }: { batch: Batch | null; onClose: () => void }) {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<BatchRows | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!batch) return;
    setPage(1);
  }, [batch?.id]);

  useEffect(() => {
    if (!batch) return;
    setLoading(true);
    getBatchRows(batch.id, page)
      .then(setData)
      .catch(() => toast.error("加载明细失败，请稍后重试"))
      .finally(() => setLoading(false));
  }, [batch?.id, page]);

  const pageCount = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <Dialog open={!!batch} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-5xl">
        <DialogHeader>
          <DialogTitle>批次 #{batch?.id} 明细</DialogTitle>
          <DialogDescription>
            {batch?.file_name}（共 {data?.total ?? "-"} 行）。订单表为脱敏后的原始行，已剔除乘机人姓名、票号等敏感字段。
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[60vh] overflow-auto rounded-md border border-border">
          {loading && !data ? (
            <div className="flex justify-center py-10">
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <table className="w-full whitespace-nowrap text-xs">
              <thead className="sticky top-0 bg-muted/80 text-left text-muted-foreground">
                <tr>
                  {data?.columns.map((c) => (
                    <th key={c} className="px-2 py-1.5 font-medium">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data?.rows.length === 0 && (
                  <tr>
                    <td className="px-3 py-8 text-center text-muted-foreground">没有明细数据</td>
                  </tr>
                )}
                {data?.rows.map((r, i) => (
                  <tr key={i} className="border-t border-border">
                    {data.columns.map((c) => (
                      <td key={c} className="px-2 py-1.5">
                        {cell(r[c])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {data && data.total > PAGE_SIZE && (
          <div className="flex items-center justify-end gap-3 text-sm">
            <Button variant="outline" size="sm" disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)}>
              上一页
            </Button>
            <span className="text-muted-foreground">
              第 {page} / {pageCount} 页
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= pageCount || loading}
              onClick={() => setPage((p) => p + 1)}
            >
              下一页
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
