from datetime import time

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


SORT_KEYS = ("query", "order", "l2b")
MAX_SUMMARY_DAYS = 60


def summarize_groups(rows, dimension: str, sort_by: str = "query") -> tuple[list[dict], dict]:
    """把查询量明细行按合作方汇总。纯函数，便于单元测试。

    rows 每行需有 d（日期）、code（合作方）、q（查询量）、quote（有报价量）、blocked（拦截量）、o（订单量）。
    sort_by=l2b 表示按表现从差到好：有查询无订单的最前，其后查订比从高到低，没有业务数据的最后。
    返回 (分组列表, 合计)。分组清单就是数据里出现的全部合作方，不依赖任何配置。
    """
    if sort_by not in SORT_KEYS:
        raise ImportValidationError(f"sort_by 只能是 {' / '.join(SORT_KEYS)}")
    agg: dict[str, dict] = {}
    for r in rows:
        key = "ALL" if dimension == "overall" else r["code"]
        g = agg.setdefault(key, {"q": 0, "quote": 0, "blocked": 0, "o": 0, "days": set()})
        g["q"] += r["q"]
        g["quote"] += r["quote"] or 0
        g["blocked"] += r.get("blocked") or 0
        g["o"] += r["o"]
        g["days"].add(r["d"])

    def build(code: str, g: dict) -> dict:
        l2b = g["q"] / g["o"] if g["o"] > 0 else None
        return {
            "partner_code": code,
            "query": g["q"],
            "quote": g["quote"],
            "quote_rate": round(g["quote"] / g["q"], 4) if g["q"] > 0 else None,
            "blocked": g["blocked"],
            "order": g["o"],
            "l2b": round(l2b, 2) if l2b is not None else None,
            "days_with_data": len(g["days"]),
            "flag": "有查询无订单" if g["q"] > 0 and g["o"] == 0 else None,
        }

    groups = [build(k, g) for k, g in agg.items()]
    if sort_by == "l2b":
        # 表现从差到好：有查询无订单（查订比无穷大）最前，其后查订比从高到低，无业务数据的最后
        groups.sort(key=lambda x: (0 if x["flag"] else (2 if x["l2b"] is None else 1), -(x["l2b"] or 0)))
    else:
        groups.sort(key=lambda x: -x[sort_by])

    total = {"q": 0, "quote": 0, "blocked": 0, "o": 0, "days": set()}
    for g in agg.values():
        total["q"] += g["q"]
        total["quote"] += g["quote"]
        total["blocked"] += g["blocked"]
        total["o"] += g["o"]
        total["days"] |= g["days"]
    return groups, build("合计", total)


def l2b_summary(
    session: Session,
    date_from: str,
    date_to: str,
    dimension: str,
    partner_code: str | None = None,
    range_type: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    sort_by: str | None = None,
    top_n: int = 50,
    *,
    user_id: int,
    erp_sid: str,
) -> dict:
    """查订比汇总（非异常类）：指定日期范围内，按渠道 / 供应商列出查询量、订单量、查订比。

    渠道范围以查询量数据为准：数据里出现的每个渠道都会列出，与是否配置佣金规则无关。
    """
    if dimension not in PTYPE:
        raise ImportValidationError("dimension 只能是 overall / channel / supplier")
    d0, d1 = parse_date(date_from), parse_date(date_to)
    if d1 < d0:
        raise ImportValidationError("结束日期不能早于开始日期")
    n_days = (d1 - d0).days + 1
    if n_days > MAX_SUMMARY_DAYS:
        raise ImportValidationError(f"日期范围最多 {MAX_SUMMARY_DAYS} 天")

    rt = range_type or "full_day"
    if rt == "full_day":
        ps, pe = FULL_DAY
    elif rt == "period":
        ps, pe = parse_time(period_start), parse_time(period_end)
        if ps is None or pe is None or ps >= pe:
            raise ImportValidationError("时间段需要提供开始和结束时间，且开始早于结束")
    else:
        raise ImportValidationError("range_type 只能是 full_day 或 period")

    days = [d0 + timedelta(days=i) for i in range(n_days)]
    if rt == "full_day":
        ensure_days(session, days, user_id, erp_sid)

    rows = session.execute(
        text(
            """SELECT biz_date AS d, partner_code AS code, query_count AS q, quote_count AS quote, blocked_count AS blocked, order_count AS o
               FROM query_stat
               WHERE user_id = :uid AND range_type = :rt AND period_start = :ps AND period_end = :pe
                 AND partner_type = :pt AND biz_date BETWEEN :d0 AND :d1
                 AND (CAST(:code AS TEXT) IS NULL OR partner_code = :code)"""
        ),
        {"uid": user_id, "rt": rt, "ps": ps, "pe": pe, "pt": PTYPE[dimension], "d0": d0, "d1": d1, "code": partner_code or None},
    ).mappings().all()
    if not rows:
        return {"日期范围": f"{d0} ～ {d1}", "结论": "没有该日期范围的查询量数据。完整一天的数据由 ERP 接口获取；时间段的数据需要上传查询量表。"}

    groups, total = summarize_groups(rows, dimension, sort_by or "query")
    shown, rest = groups[:top_n], groups[top_n:]
    result = {
        "日期范围": f"{d0} ～ {d1}",
        "维度": dimension,
        "范围": "完整一天" if rt == "full_day" else f"{ps.strftime('%H:%M')}～{pe.strftime('%H:%M')}",
        "天数": n_days,
        "有数据的天数": total["days_with_data"],
        "合计": total,
        "组数": len(groups),
        f"分组明细（{'表现从差到好' if (sort_by or 'query') == 'l2b' else (sort_by or 'query') + ' 从高到低'}，最多 {top_n} 条）": shown,
        "说明": "分组清单来自查询量数据中出现的全部渠道/供应商，与佣金规则配置无关；查订比 = 查询量 / 订单量，越高表示查得多订得少；"
                "quote 为有报价量，quote_rate 为有报价率，blocked 为被拦截次数。",
    }
    if rest:
        _, rest_total = summarize_groups(
            [{"d": None, "code": g["partner_code"], "q": g["query"], "quote": g["quote"], "blocked": g["blocked"], "o": g["order"]} for g in rest],
            dimension,
        )
        rest_total.pop("days_with_data")
        result["未展示的其余分组合计"] = {**rest_total, "partner_code": f"其余 {len(rest)} 个"}
    if d1 >= beijing_today():
        result["注意"] = "范围包含当天，当天数据是截至查询时刻的累计值，不是完整一天。"
    return result
