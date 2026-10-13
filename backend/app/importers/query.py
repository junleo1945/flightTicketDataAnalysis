from sqlalchemy.orm import Session

from app.core.upsert import upsert
from app.importers.common import ImportValidationError, Window, check_columns, open_workbook, read_sheet, text, to_int
from app.models.analysis import QueryStat
from app.models.upload import UploadBatch

# 查询量表字段（样例文件未提供，待 Q18 确认，列名以方案 2.2 为准）
PARTNER_TYPE = {"供应商": "supplier", "渠道": "channel"}
METRICS = {
    "查询量": "query_count",
    "有报价量": "quote_count",
    "订单量": "order_count",
    "航段量": "segment_count",
    "被拦截次数": "blocked_count",
}
REQUIRED = ["合作类型", "合作方代码", *METRICS]


def build_query_rows(sheet_rows, window: Window, batch_id: int, user_id: int) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row_no, d in sheet_rows:
        ptype_raw = text(d.get("合作类型"))
        if ptype_raw not in PARTNER_TYPE:
            raise ImportValidationError(f"第 {row_no} 行合作类型无法识别：{ptype_raw}（应为 供应商 / 渠道）")
        code = text(d.get("合作方代码"))
        if not code:
            raise ImportValidationError(f"第 {row_no} 行缺少合作方代码")
        ptype = PARTNER_TYPE[ptype_raw]
        if (ptype, code) in seen:
            raise ImportValidationError(f"第 {row_no} 行与前面的记录重复：{ptype_raw}/{code}")
        seen.add((ptype, code))
        item = {
            "user_id": user_id,
            "biz_date": window.biz_date,
            "range_type": window.range_type,
            "period_start": window.start,
            "period_end": window.end,
            "partner_type": ptype,
            "partner_code": code,
            "batch_id": batch_id,
        }
        for header, field in METRICS.items():
            item[field] = to_int(d.get(header))
        rows.append(item)
    return rows


def import_query(session: Session, batch: UploadBatch, data: bytes, window: Window) -> dict:
    wb = open_workbook(data)
    headers, sheet_rows = read_sheet(wb.worksheets[0])
    check_columns(headers, REQUIRED, wb.worksheets[0].title)
    rows = build_query_rows(sheet_rows, window, batch.id, batch.user_id or 0)
    upsert(
        session.connection(),
        QueryStat.__table__,
        rows,
        ["user_id", "biz_date", "range_type", "period_start", "period_end", "partner_type", "partner_code"],
    )
    batch.row_count = len(rows)
    return {"记录数": len(rows), "渠道": sum(r["partner_type"] == "channel" for r in rows), "供应商": sum(r["partner_type"] == "supplier" for r in rows)}
