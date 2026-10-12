from sqlalchemy.orm import Session

from app.core.upsert import upsert
from app.importers.common import ImportValidationError, Window, check_columns, dec, open_workbook, read_sheet, text, to_int
from app.models.analysis import SupplyQuality
from app.models.upload import UploadBatch

# 表头 → 字段。两种格式共用：「航司 × 供应」和「航司 × 渠道 × 供应」（见 Q17）
METRICS = {
    "验价总数": "verify_total",
    "验价超时5s": "timeout_5s",
    "验价超时10s": "timeout_10s",
    "验价成功数": "verify_success",
    "验价失败数": "verify_fail",
    "供应商成功数": "supplier_success",
    "供应商失败数": "supplier_fail",
    "供应商变价": "price_change",  # 仅新格式有
    "未预定": "not_booked",
    "预定成功": "book_success",
    "预定失败": "book_fail",
    "预定率": "book_rate",
    "超时率": "timeout_rate",
}
OPTIONAL = {"供应商变价", "agent_code"}
RATE_FIELDS = {"book_rate", "timeout_rate"}
REQUIRED = ["航司", "supplier_code", *(h for h in METRICS if h not in OPTIONAL)]
KEY = ["biz_date", "range_type", "period_start", "period_end", "airline_code", "agent_code", "supplier_code"]


def build_quality_rows(sheet_rows, window: Window, batch_id: int) -> tuple[list[dict], list[int]]:
    """返回 (可入库的行, 因缺少航司或供应商代码而跳过的 Excel 行号)。"""
    rows: list[dict] = []
    skipped: list[int] = []
    seen: set[tuple[str, str, str]] = set()
    for row_no, d in sheet_rows:
        airline = text(d.get("航司"))
        supplier = text(d.get("supplier_code"))
        agent = text(d.get("agent_code"))  # 供应版没有此列，记为空串
        if not airline or not supplier:
            skipped.append(row_no)  # 源数据缺字段，跳过而不是整个文件失败
            continue
        key = (airline, agent, supplier)
        if key in seen:
            raise ImportValidationError(f"第 {row_no} 行与前面的记录重复：{airline}/{agent}/{supplier}")
        seen.add(key)
        item = {
            "biz_date": window.biz_date,
            "range_type": window.range_type,
            "period_start": window.start,
            "period_end": window.end,
            "airline_code": airline,
            "agent_code": agent,
            "supplier_code": supplier,
            "batch_id": batch_id,
        }
        for header, field in METRICS.items():
            raw = d.get(header)
            if field in RATE_FIELDS:
                item[field] = dec(raw)
            elif field == "price_change":
                item[field] = to_int(raw) if text(raw) else None
            else:
                item[field] = to_int(raw)
        rows.append(item)
    return rows, skipped


def import_quality(session: Session, batch: UploadBatch, data: bytes, window: Window) -> dict:
    wb = open_workbook(data)
    ws = wb.worksheets[0]
    headers, sheet_rows = read_sheet(ws)
    check_columns(headers, REQUIRED, ws.title)
    rows, skipped = build_quality_rows(sheet_rows, window, batch.id)
    upsert(session.connection(), SupplyQuality.__table__, rows, KEY)
    batch.row_count = len(rows)
    fmt = "渠道×供应" if "agent_code" in headers else "供应"
    return {"记录数": len(rows), "格式": fmt, "跳过行数": len(skipped), "跳过的 Excel 行号（前20）": skipped[:20]}
