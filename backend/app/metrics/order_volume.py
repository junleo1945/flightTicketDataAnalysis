from collections import defaultdict
from datetime import time

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.importers.common import ImportValidationError
from app.metrics.common import (
    STATUS_DONE,
    history_range,
    latest_time,
    parse_date,
    parse_threshold,
    parse_time,
    parse_x,
    pct,
    window_text,
)

# 维度 → (口径, 分组字段)。口径 channel=按渠道订单（途灵订单号）；supplier=按供应订单（TM供应商订单号）
DIMENSIONS = {
    "overall": ("channel", []),
    "channel": ("channel", ["channel_code"]),
    "channel_airline": ("channel", ["channel_code", "airline_code"]),
    "channel_airline_route": ("channel", ["channel_code", "airline_code", "route"]),
    "supplier": ("supplier", ["supplier_code"]),
    "supplier_airline": ("supplier", ["supplier_code", "airline_code"]),
    "supplier_airline_route": ("supplier", ["supplier_code", "airline_code", "route"]),
}


def _sql_parts(basis: str, need_airline: bool, need_route: bool):
    """返回 (表, 关联, 日期列, 时间列, 计数列, 状态列, 字段映射, 用户列)。关联与子表都按用户对齐。"""
    if basis == "channel":
        frm, pk, uc = "tm_order o", "o.tm_order_no", "o.user_id"
        day, tm = "o.pay_date", "CAST(o.pay_time AS TIME)"
        ident, status = "o.tm_order_no", "o.order_status"
        cols = {"channel_code": "o.channel_code"}
    else:
        frm, pk, uc = "supplier_order s", "s.tm_order_no", "s.user_id"
        day, tm = "CAST(s.pay_time_effective AS DATE)", "CAST(s.pay_time_effective AS TIME)"
        ident, status = "s.tm_supplier_order_no", "s.status"
        cols = {"supplier_code": "s.supplier_code"}
    joins = []
    if need_airline:
        joins.append(f"JOIN order_airline a ON a.tm_order_no = {pk} AND a.user_id = {uc}")
        cols["airline_code"] = "a.airline_code"
    if need_route:
        joins.append(f"JOIN order_route r ON r.tm_order_no = {pk} AND r.user_id = {uc}")
        cols["route"] = "r.route"
    return frm, joins, day, tm, ident, status, cols, uc


def order_volume_anomaly(
    session: Session,
    date_str: str,
    x,
    dimension: str,
    filters: dict | None = None,
    window_end: str | None = None,
    threshold=None,
    top_n: int = 20,
    *,
    user_id: int,
) -> dict:
    """场景 1：订单量异常。当前窗口单量 vs 前 x 天同时段日均单量。"""
    if dimension not in DIMENSIONS:
        raise ImportValidationError(f"dimension 只能是：{', '.join(DIMENSIONS)}")
    filters = {k: v for k, v in (filters or {}).items() if v}
    day = parse_date(date_str)
    n = parse_x(x)
    thr = parse_threshold(threshold)
    h0, h1 = history_range(day, n)
    basis, keys = DIMENSIONS[dimension]

    need_air = "airline_code" in keys or "airline_code" in filters
    need_route = "route" in keys or "route" in filters
    frm, joins, dexpr, texpr, ident, status, cols, uc = _sql_parts(basis, need_air, need_route)

    wend = parse_time(window_end)
    wend_note = None
    if wend is None:
        if basis == "channel":
            wend = latest_time(
                session, "SELECT MAX(pay_time) FROM tm_order WHERE pay_date = :d AND order_status = :s AND user_id = :uid",
                {"d": day, "s": STATUS_DONE, "uid": user_id},
            )
        else:
            wend = latest_time(
                session,
                "SELECT MAX(pay_time_effective) FROM supplier_order WHERE CAST(pay_time_effective AS DATE) = :d AND status = :s AND user_id = :uid",
                {"d": day, "s": STATUS_DONE, "uid": user_id},
            )
        # 当天没有订单（如零出单）时按整天计算，才能判断出“零出单”这类异常
        wend_note = "当天没有订单数据，按整天（23:59:59）计算" if wend is None else None
        wend = wend or time(23, 59, 59)

    params: dict = {"h0": h0, "day": day, "wend": wend, "uid": user_id}
    base_where = [f"{status} = '{STATUS_DONE}'", f"{dexpr} BETWEEN :h0 AND :day", f"{texpr} <= :wend", f"{uc} = :uid"]

    # 有效历史天数：按未分组的整体数据判断（不受维度过滤影响）
    base_days_rows = session.execute(
        text(f"SELECT {dexpr} AS d, COUNT(DISTINCT {ident}) AS cnt FROM {frm} WHERE {' AND '.join(base_where)} GROUP BY 1"),
        params,
    ).mappings().all()
    valid_days = {r["d"] for r in base_days_rows if r["cnt"] > 0 and r["d"] < day}

    where = list(base_where)
    for k, v in filters.items():
        if k not in cols:
            raise ImportValidationError(f"维度 {dimension} 不支持过滤条件 {k}")
        where.append(f"{cols[k]} = :f_{k}")
        params[f"f_{k}"] = v

    select = [f"{dexpr} AS d"] + [f"{cols[k]} AS {k}" for k in keys] + [f"COUNT(DISTINCT {ident}) AS cnt"]
    group_by = ", ".join(str(i + 1) for i in range(len(keys) + 1))
    sql = f"SELECT {', '.join(select)} FROM {frm} {' '.join(joins)} WHERE {' AND '.join(where)} GROUP BY {group_by}"
    rows = session.execute(text(sql), params).mappings().all()

    counts: dict[tuple, dict] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        counts[tuple(r[k] for k in keys)][r["d"]] += r["cnt"]

    groups = []
    for gk, by_day in counts.items():
        cur = by_day.get(day, 0)
        hist = sum(v for d, v in by_day.items() if d in valid_days)
        baseline = hist / len(valid_days) if valid_days else None
        change = (cur - baseline) / baseline if baseline and baseline > 0 else None
        groups.append({
            "dims": dict(zip(keys, gk)),
            "current": cur,
            "baseline_daily_avg": baseline,
            "change_rate": change,
            "change_rate_text": pct(change),
            "anomaly": change is not None and change < thr,
        })

    total = None
    if dimension in ("overall", "channel", "supplier"):  # 无多值拆分，合计不会重复计数
        cur_total = sum(g["current"] for g in groups)
        base_total = sum(g["baseline_daily_avg"] or 0 for g in groups) if valid_days else None
        t_change = (cur_total - base_total) / base_total if base_total else None
        total = {"current": cur_total, "baseline_daily_avg": base_total, "change_rate": t_change, "change_rate_text": pct(t_change)}

    anomalies = sorted((g for g in groups if g["anomaly"]), key=lambda g: g["change_rate"])
    return {
        "日期": str(day),
        "维度": dimension,
        "口径": "渠道订单（采购维度，按途灵订单号去重，支付时间落窗口，含已取消）" if basis == "channel"
        else "供应订单（供应维度，出票完成，按 TM供应商订单号去重，供应商订单支付时间落窗口）",
        **window_text(day, wend, h0, h1),
        "x": n,
        "有效历史天数": len(valid_days),
        "阈值": thr,
        "整体合计": total,
        "组数": len(groups),
        "异常组数": len(anomalies),
        "异常明细（变化率从低到高）": anomalies[:top_n],
        "备注": "历史有效天数不足 x 天时，日均按实际有效天数计算；无有效历史数据时不判断异常。" + (f" {wend_note}。" if wend_note else ""),
    }
