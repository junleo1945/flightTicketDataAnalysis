from datetime import time

from sqlalchemy import text
from sqlalchemy.orm import Session

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
) -> dict:
    """场景 2：查订比异常。查订比 = 查询量 / 订单量，对比前 x 天的汇总查订比基线。

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

    rows = session.execute(
        text(
            """SELECT biz_date AS d, partner_code AS code, query_count AS q, order_count AS o
               FROM query_stat
               WHERE range_type = :rt AND period_start = :ps AND period_end = :pe
                 AND partner_type = :pt AND biz_date BETWEEN :h0 AND :day
                 AND (CAST(:code AS TEXT) IS NULL OR partner_code = :code)"""
        ),
        {"rt": rt, "ps": ps, "pe": pe, "pt": PTYPE[dimension], "h0": h0, "day": day, "code": partner_code or None},
    ).mappings().all()
    if not rows:
        return {"日期": str(day), "结论": "查询量表中没有该日期与范围的数据，无法判断。请先上传对应的查询量表。"}

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
    return {
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
