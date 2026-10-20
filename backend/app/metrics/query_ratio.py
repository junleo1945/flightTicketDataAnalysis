from datetime import date, time

from sqlalchemy import text
from sqlalchemy.orm import Session

from datetime import timedelta

from app.erp.cache import beijing_today, ensure_days
from app.importers.common import ImportValidationError
from app.metrics.common import history_range, parse_date, parse_threshold, parse_time, parse_x, pct

PTYPE = {"overall": "channel", "channel": "channel", "supplier": "supplier"}
FULL_DAY = (time(0, 0, 0), time(23, 59, 59))


def l2b_anomaly(
    session: Session,
    date_str: str,
    x,
    dimension: str,
    partner_code: str | None = None,
    range_type: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    threshold=None,
    top_n: int = 20,
    *,
    user_id: int,
    erp_sid: str,
) -> dict:
    """场景 2：查订比异常。查询量数据只取该用户自己的（上传的或用其 ERP 会话拉取的）。查订比 = 查询量 / 订单量，对比前 x 天的汇总查订比基线。

    overall 取全部渠道行之和（查询量表中供应商行与渠道行是不同侧的查询，不能直接相加）。
    """
    if dimension not in PTYPE:
        raise ImportValidationError("dimension 只能是 overall / channel / supplier")
    day = parse_date(date_str)
    n = parse_x(x)
    thr = parse_threshold(threshold)
    h0, h1 = history_range(day, n)

    rt = range_type or "full_day"
    if rt == "full_day":
        ps, pe = FULL_DAY
    elif rt == "period":
        ps, pe = parse_time(period_start), parse_time(period_end)
        if ps is None or pe is None or ps >= pe:
            raise ImportValidationError("时间段需要提供开始和结束时间，且开始早于结束")
    else:
        raise ImportValidationError("range_type 只能是 full_day 或 period")

    if rt == "full_day":
        ensure_days(session, [h0 + timedelta(days=i) for i in range((day - h0).days + 1)], user_id, erp_sid)

    rows = session.execute(
        text(
            """SELECT biz_date AS d, partner_code AS code, query_count AS q, order_count AS o
               FROM query_stat
               WHERE user_id = :uid AND range_type = :rt AND period_start = :ps AND period_end = :pe
                 AND partner_type = :pt AND biz_date BETWEEN :h0 AND :day
                 AND (CAST(:code AS TEXT) IS NULL OR partner_code = :code)"""
        ),
        {"uid": user_id, "rt": rt, "ps": ps, "pe": pe, "pt": PTYPE[dimension], "h0": h0, "day": day, "code": partner_code or None},
    ).mappings().all()
    if not rows:
        return {"日期": str(day), "结论": "没有该日期与范围的查询量数据，无法判断。完整一天的数据由 ERP 接口获取；时间段的数据需要上传查询量表。"}

    # 按合作方（overall 时合并为一个组）汇总：{组: {日期: (查询量, 订单量)}}
    agg: dict[str, dict] = {}
    for r in rows:
        key = "ALL" if dimension == "overall" else r["code"]
        bucket = agg.setdefault(key, {})
        q0, o0 = bucket.get(r["d"], (0, 0))
        bucket[r["d"]] = (q0 + r["q"], o0 + r["o"])

    groups = []
    for key, by_day in agg.items():
        cur_q, cur_o = by_day.get(day, (0, 0))
        hist = [v for d, v in by_day.items() if h0 <= d < day]
        hq = sum(q for q, _ in hist)
        ho = sum(o for _, o in hist)
        valid_days = len(hist)

        flag = None
        if cur_o == 0 and cur_q > 0:
            flag = "有查询无订单"
        elif cur_o == 0 and cur_q == 0:
            flag = "无业务数据"

        cur_l2b = cur_q / cur_o if cur_o > 0 else None
        base = hq / ho if ho > 0 else None
        change = (cur_l2b - base) / base if cur_l2b is not None and base and base > 0 else None
        anomaly = flag == "有查询无订单" or (change is not None and change < thr)
        if flag == "无业务数据":
            anomaly = False
        groups.append({
            "partner_code": key,
            "current_query": cur_q,
            "current_order": cur_o,
            "current_l2b": cur_l2b,
            "baseline_l2b": base,
            "valid_history_days": valid_days,
            "change_rate": change,
            "change_rate_text": pct(change),
            "flag": flag,
            "anomaly": anomaly,
        })

    anomalies = sorted(
        (g for g in groups if g["anomaly"]),
        key=lambda g: (0 if g["flag"] else 1, g["change_rate"] if g["change_rate"] is not None else 0),
    )
    result = {
        "日期": str(day),
        "维度": dimension,
        "范围": "完整一天" if rt == "full_day" else f"{ps.strftime('%H:%M')}～{pe.strftime('%H:%M')}",
        "历史窗口": f"{h0}～{h1}（相同范围）",
        "x": n,
        "阈值": thr,
        "组数": len(groups),
        "异常组数": len(anomalies),
        "异常明细（变化率从低到高，有查询无订单的排最前）": anomalies[:top_n],
        "备注": "基线 = 历史窗口 Σ查询量 / Σ订单量；历史基线为 0 时不判断变化率；有查询无订单单独标记。",
    }
    if day == beijing_today():
        result["注意"] = "当天数据是截至查询时刻的累计值（ERP 接口只支持按日期查询），与历史整天数据不是同一时间段，变化率仅供参考。"
    return result


MAX_DAILY_SPAN = 31  # 按日取原始值时最多跨的天数，每个没缓存的历史日要调一次 ERP 接口


def build_daily_series(day_from: date, day_to: date, by_day: dict[date, tuple[int, int]]) -> dict:
    """把按日汇总的 (查询量, 订单量) 铺成连续日期序列，没有数据的日期显式标出，并给出合计。"""
    days, tq, to = [], 0, 0
    for i in range((day_to - day_from).days + 1):
        d = day_from + timedelta(days=i)
        if d not in by_day:
            days.append({"日期": str(d), "查询量": None, "订单量": None, "查订比": None, "备注": "没有数据"})
            continue
        q, o = by_day[d]
        tq, to = tq + q, to + o
        days.append({"日期": str(d), "查询量": q, "订单量": o, "查订比": round(q / o, 2) if o > 0 else None})
    return {
        "日明细": days,
        "合计": {"查询量": tq, "订单量": to, "查订比": round(tq / to, 2) if to > 0 else None, "有数据天数": len(by_day)},
    }


def l2b_daily(
    session: Session,
    date_from: str,
    date_to: str,
    dimension: str = "overall",
    partner_code: str | None = None,
    *,
    user_id: int,
    erp_sid: str,
) -> dict:
    """按日返回完整一天的查询量、订单量、查订比原始值（不做异常判断）。数据与查订比异常用同一份缓存。

    overall 与 channel 取渠道行（overall 为全部渠道之和），supplier 取供应商行；
    渠道侧与供应商侧是不同侧的查询，不能相加。partner_code 不填则汇总该侧全部合作方。
    """
    if dimension not in PTYPE:
        raise ImportValidationError("dimension 只能是 overall / channel / supplier")
    d0, d1 = parse_date(date_from), parse_date(date_to)
    if d0 > d1:
        raise ImportValidationError("开始日期不能晚于结束日期")
    if (d1 - d0).days + 1 > MAX_DAILY_SPAN:
        raise ImportValidationError(f"一次最多查询 {MAX_DAILY_SPAN} 天，请缩小日期范围")

    ensure_days(session, [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)], user_id, erp_sid)
    rows = session.execute(
        text(
            """SELECT biz_date AS d, SUM(query_count) AS q, SUM(order_count) AS o
               FROM query_stat
               WHERE user_id = :uid AND range_type = 'full_day' AND period_start = :ps AND period_end = :pe
                 AND partner_type = :pt AND biz_date BETWEEN :d0 AND :d1
                 AND (CAST(:code AS TEXT) IS NULL OR partner_code = :code)
               GROUP BY biz_date"""
        ),
        {"uid": user_id, "ps": FULL_DAY[0], "pe": FULL_DAY[1], "pt": PTYPE[dimension], "d0": d0, "d1": d1, "code": partner_code or None},
    ).mappings().all()
    by_day = {r["d"]: (int(r["q"]), int(r["o"])) for r in rows}

    result = {
        "日期范围": f"{d0}～{d1}",
        "维度": dimension,
        "合作方": partner_code or "全部",
        "范围": "完整一天",
        **build_daily_series(d0, d1, by_day),
        "备注": "查订比 = 查询量 / 订单量，订单量为 0 时不计算；渠道侧与供应商侧是不同侧的查询，不能相加；这里的订单量来自 ERP 查订比接口，与上传的渠道订单是两份独立来源。",
    }
    if d1 == beijing_today():
        result["注意"] = "当天数据是截至查询时刻的累计值，与历史整天数据不是同一时间段。"
    return result
