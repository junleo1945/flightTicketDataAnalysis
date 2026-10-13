import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from app.core.upsert import upsert
from app.importers.common import (
    ImportValidationError,
    check_columns,
    first_nonzero,
    first_text,
    first_ts,
    open_workbook,
    read_sheet,
    split_csv,
    sum_dec,
    text,
    to_int,
)
from app.models.order import OrderAirline, OrderRoute, SupplierOrder, TmOrder
from app.models.upload import RawOrderRow, UploadBatch

# 敏感字段不入库（方案 Q10）
EXCLUDED_FIELDS = {"乘机人姓名", "票号", "PNR", "航司大编码"}

PURCHASE_SHEET = "采购维度"
SUPPLY_SHEET = "供应维度"
PURCHASE_COLS = [
    "途灵订单号", "客户订单号", "采购客户", "订单类型", "创建时间", "支付时间", "出票时间", "订单状态",
    "订单总价", "币种", "支付方式", "出发日期", "航司", "行程", "航段数",
]
SUPPLY_COLS = [
    "途灵订单号", "TM供应商订单号", "供应商订单号", "供应商代码", "供应商订单状态", "供应商订单支付时间",
    "供应商订单创建时间", "供应商订单出票时间", "供应商币种", "供应商总价", "供应商总价CNY", "供应商总价USD",
    "供应币种兑CNY汇率", "供应币种兑USD汇率", "订单总价", "订单总价CNY", "订单总价USD",
    "币种兑CNY汇率", "币种兑USD汇率",
]


@dataclass
class OrderRecords:
    tm_orders: list[dict] = field(default_factory=list)
    supplier_orders: list[dict] = field(default_factory=list)
    airlines: list[dict] = field(default_factory=list)
    routes: list[dict] = field(default_factory=list)
    raw_rows: list[dict] = field(default_factory=list)
    pay_min: datetime | None = None
    pay_max: datetime | None = None
    summary: dict = field(default_factory=dict)


def _sanitize(d: dict) -> dict:
    kept = {k: v for k, v in d.items() if k not in EXCLUDED_FIELDS}
    return json.loads(json.dumps(kept, ensure_ascii=False, default=str))


def _unique(items) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


def build_order_records(purchase: list[tuple[int, dict]], supply: list[tuple[int, dict]], batch_id: int, user_id: int) -> OrderRecords:
    """把两个 sheet 转成待入库的记录。纯函数，不访问数据库，便于单元测试。

    规则要点：
    - 渠道订单按途灵订单号一行，金额取采购维度各行之和（金额只在每组第一行有值）。
    - 供应订单按 TM供应商订单号一行，金额取各行之和。
    - 渠道订单的 CNY/USD 金额和汇率取供应维度的第一个非零值（同一途灵订单下各供应行的整单金额会重复，不能累加）。
    - 供应订单支付时间兜底（Q16）：供应商订单支付时间 → 渠道订单支付时间 → 供应商订单出票时间。
    """
    rec = OrderRecords()

    purch: dict[str, list[dict]] = defaultdict(list)
    for row_no, d in purchase:
        tm = text(d.get("途灵订单号"))
        if not tm:
            raise ImportValidationError(f"采购维度第 {row_no} 行缺少途灵订单号")
        purch[tm].append(d)
        rec.raw_rows.append({"batch_id": batch_id, "sheet_name": PURCHASE_SHEET, "row_no": row_no, "data": _sanitize(d)})

    supp_by_tm: dict[str, list[dict]] = defaultdict(list)
    supp_by_sid: dict[str, list[dict]] = defaultdict(list)
    for row_no, d in supply:
        tm = text(d.get("途灵订单号"))
        sid = text(d.get("TM供应商订单号"))
        if not tm:
            raise ImportValidationError(f"供应维度第 {row_no} 行缺少途灵订单号")
        if not sid:
            raise ImportValidationError(f"供应维度第 {row_no} 行缺少 TM供应商订单号")
        supp_by_tm[tm].append(d)
        supp_by_sid[sid].append(d)
        rec.raw_rows.append({"batch_id": batch_id, "sheet_name": SUPPLY_SHEET, "row_no": row_no, "data": _sanitize(d)})

    orphans = set(supp_by_tm) - set(purch)
    if orphans:
        raise ImportValidationError(f"供应维度中有 {len(orphans)} 个途灵订单在采购维度中找不到，例如 {next(iter(orphans))}")

    # 渠道订单
    tm_pay: dict[str, datetime | None] = {}
    for tm, ds in purch.items():
        pay = first_ts(ds, "支付时间")
        tm_pay[tm] = pay
        srows = supp_by_tm.get(tm, [])
        airlines = _unique(a for d in ds for a in split_csv(d.get("航司")))
        routes = _unique(r for d in ds for r in split_csv(d.get("行程")))
        rec.tm_orders.append({
            "user_id": user_id,
            "tm_order_no": tm,
            "channel_code": first_text(ds, "采购客户"),
            "customer_order_no": first_text(ds, "客户订单号") or None,
            "order_type": first_text(ds, "订单类型") or None,
            "order_status": first_text(ds, "订单状态"),
            "create_time": first_ts(ds, "创建时间"),
            "pay_time": pay,
            "pay_date": pay.date() if pay else None,
            "ticket_time": first_ts(ds, "出票时间"),
            "currency": first_text(ds, "币种") or None,
            "total_price": sum_dec(ds, "订单总价"),
            "rate_cny": first_nonzero(srows, "币种兑CNY汇率"),
            "total_cny": first_nonzero(srows, "订单总价CNY"),
            "rate_usd": first_nonzero(srows, "币种兑USD汇率"),
            "total_usd": first_nonzero(srows, "订单总价USD"),
            "pay_method": first_text(ds, "支付方式") or None,
            "depart_time": first_ts(ds, "出发日期"),
            "segment_count": to_int(first_text(ds, "航段数") or 0),
            "passenger_count": len(ds),
            "airline_codes": ",".join(airlines) or None,
            "batch_id": batch_id,
        })
        rec.airlines += [{"user_id": user_id, "tm_order_no": tm, "airline_code": a} for a in airlines]
        rec.routes += [{"user_id": user_id, "tm_order_no": tm, "route": r} for r in routes]

    # 供应订单
    fallback = 0
    cancelled = 0
    for sid, ds in supp_by_sid.items():
        tm = text(ds[0].get("途灵订单号"))
        status = first_text(ds, "供应商订单状态")
        cancelled += status == "订单取消"
        pay = first_ts(ds, "供应商订单支付时间")
        ticket = first_ts(ds, "供应商订单出票时间")
        if pay:
            eff, src = pay, "supplier_pay"
        elif tm_pay.get(tm):
            eff, src = tm_pay[tm], "tm_pay"
            fallback += 1
        elif ticket:
            eff, src = ticket, "ticket"
            fallback += 1
        else:
            eff, src = None, None
        rec.supplier_orders.append({
            "user_id": user_id,
            "tm_supplier_order_no": sid,
            "tm_order_no": tm,
            "supplier_order_no": first_text(ds, "供应商订单号") or None,
            "supplier_code": first_text(ds, "供应商代码"),
            "status": status,
            "create_time": first_ts(ds, "供应商订单创建时间"),
            "pay_time": pay,
            "pay_time_effective": eff,
            "pay_time_source": src,
            "ticket_time": ticket,
            "currency": first_text(ds, "供应商币种") or None,
            "supplier_total": sum_dec(ds, "供应商总价"),
            "supplier_total_cny": sum_dec(ds, "供应商总价CNY"),
            "supplier_total_usd": sum_dec(ds, "供应商总价USD"),
            "rate_cny": first_nonzero(ds, "供应币种兑CNY汇率"),
            "rate_usd": first_nonzero(ds, "供应币种兑USD汇率"),
            "passenger_count": len(ds),
            "batch_id": batch_id,
        })

    pays = [t for t in tm_pay.values() if t]
    rec.pay_min = min(pays) if pays else None
    rec.pay_max = max(pays) if pays else None
    rec.summary = {
        "采购维度行数": len(purchase),
        "供应维度行数": len(supply),
        "渠道订单数": len(rec.tm_orders),
        "供应订单数": len(rec.supplier_orders),
        "其中订单取消": cancelled,
        "供应支付时间兜底数": fallback,
    }
    return rec


def save_order_records(session: Session, rec: OrderRecords, user_id: int) -> None:
    conn = session.connection()
    upsert(conn, TmOrder.__table__, rec.tm_orders, ["user_id", "tm_order_no"])
    upsert(conn, SupplierOrder.__table__, rec.supplier_orders, ["user_id", "tm_supplier_order_no"])

    # 航司、航线为派生数据，按（用户，订单）整体替换
    tm_nos = [r["tm_order_no"] for r in rec.tm_orders]
    for child in (OrderAirline, OrderRoute):
        if tm_nos:
            conn.execute(
                delete(child.__table__).where(
                    child.__table__.c.user_id == user_id, child.__table__.c.tm_order_no.in_(tm_nos)
                )
            )
    if rec.airlines:
        conn.execute(insert(OrderAirline.__table__), rec.airlines)
    if rec.routes:
        conn.execute(insert(OrderRoute.__table__), rec.routes)
    if rec.raw_rows:
        conn.execute(insert(RawOrderRow.__table__), rec.raw_rows)


def import_orders(session: Session, batch: UploadBatch, data: bytes) -> dict:
    wb = open_workbook(data)
    for name in (PURCHASE_SHEET, SUPPLY_SHEET):
        if name not in wb.sheetnames:
            raise ImportValidationError(f"订单表缺少 sheet「{name}」")
    p_headers, p_rows = read_sheet(wb[PURCHASE_SHEET])
    s_headers, s_rows = read_sheet(wb[SUPPLY_SHEET])
    check_columns(p_headers, PURCHASE_COLS, PURCHASE_SHEET)
    check_columns(s_headers, SUPPLY_COLS, SUPPLY_SHEET)

    rec = build_order_records(p_rows, s_rows, batch.id, batch.user_id or 0)
    save_order_records(session, rec, batch.user_id or 0)

    batch.data_min_time = rec.pay_min
    batch.data_max_time = rec.pay_max
    batch.row_count = len(rec.tm_orders)
    return rec.summary
