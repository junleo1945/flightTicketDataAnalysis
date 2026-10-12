from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from io import BytesIO

import openpyxl


class ImportValidationError(ValueError):
    """业务校验错误，会直接返回给用户看。"""


@dataclass(frozen=True)
class Window:
    """查询量、数据质量表的时间范围（由用户在上传时选择，见 Q2/Q17）。"""

    range_type: str  # full_day / period
    biz_date: date
    start: time
    end: time


def build_window(range_type: str, biz_date: str, start: str | None, end: str | None) -> Window:
    if not biz_date:
        raise ImportValidationError("请选择业务日期")
    try:
        day = date.fromisoformat(biz_date)
    except ValueError as exc:
        raise ImportValidationError("业务日期格式应为 YYYY-MM-DD") from exc
    if range_type == "full_day":
        return Window("full_day", day, time(0, 0, 0), time(23, 59, 59))
    if range_type == "period":
        try:
            s, e = time.fromisoformat(start or ""), time.fromisoformat(end or "")
        except ValueError as exc:
            raise ImportValidationError("时间段格式应为 HH:MM") from exc
        if s >= e:
            raise ImportValidationError("时间段的开始时间必须早于结束时间")
        return Window("period", day, s, e)
    raise ImportValidationError("范围类型只能是 full_day（完整一天）或 period（时间段）")


def open_workbook(data: bytes):
    try:
        # 不用 read_only：部分导出文件的表格范围信息不准确，只读模式会漏掉数据
        return openpyxl.load_workbook(BytesIO(data), data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ImportValidationError("无法解析 Excel 文件，请确认是 .xlsx 格式") from exc


def read_sheet(ws) -> tuple[list[str], list[tuple[int, dict]]]:
    """返回表头和 [(Excel 行号, {表头: 值})]，跳过全空行。"""
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    if not header:
        return [], []
    headers = [str(h).strip() if h is not None else "" for h in header]
    out: list[tuple[int, dict]] = []
    for row_no, values in enumerate(rows, start=2):
        if values is None or all(v in (None, "") for v in values):
            continue
        out.append((row_no, {h: (values[i] if i < len(values) else None) for i, h in enumerate(headers) if h}))
    return headers, out


def check_columns(headers: list[str], required: list[str], sheet_name: str) -> None:
    missing = [c for c in required if c not in headers]
    if missing:
        raise ImportValidationError(f"「{sheet_name}」缺少字段：{'、'.join(missing)}")


def text(v) -> str:
    return "" if v is None else str(v).strip()


def dec_opt(v) -> Decimal | None:
    s = text(v).replace(",", "")
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation as exc:
        raise ImportValidationError(f"数字格式无法识别：{s}") from exc


def dec(v) -> Decimal:
    return dec_opt(v) or Decimal("0")


def to_int(v) -> int:
    return int(dec(v))


def ts(v) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ImportValidationError(f"时间格式无法识别：{s}")


def split_csv(v) -> list[str]:
    return [x.strip() for x in text(v).split(",") if x.strip()]


def first_text(rows: list[dict], key: str) -> str:
    for d in rows:
        s = text(d.get(key))
        if s:
            return s
    return ""


def first_ts(rows: list[dict], key: str) -> datetime | None:
    for d in rows:
        t = ts(d.get(key))
        if t is not None:
            return t
    return None


def first_nonzero(rows: list[dict], key: str) -> Decimal | None:
    for d in rows:
        v = dec_opt(d.get(key))
        if v:
            return v
    return None


def sum_dec(rows: list[dict], key: str) -> Decimal:
    return sum((dec(d.get(key)) for d in rows), Decimal("0"))
