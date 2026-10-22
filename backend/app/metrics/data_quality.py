"""验价生单数据质量分析（产品《数据质量分析规则》）。

铁律：聚合先对数量字段求和，再算比率；分母为 0 一律返回「无有效数据」，不返回 0%；
不平均源数据里的比率字段。问题判断使用未四舍五入的原始比率（这里用整数乘法比较，避免浮点误差）。
"""

from collections import defaultdict
from datetime import date, time

from sqlalchemy.orm import Session

from app.erp.cache import beijing_today
from app.erp.data_quality import COUNT_FIELDS, DataQualityRow
from app.erp.quality_cache import MAX_PERIOD_DAYS, load_rows
from app.importers.common import ImportValidationError
from app.metrics.common import parse_date, parse_time

NO_DATA = "无有效数据"
UNMARKED = "未标记问题"
SMALL_SAMPLE = "小样本，仅展示"
LEVEL_KEY = "重点关注", "需要关注", SMALL_SAMPLE  # 排序时的先后；其余（含无有效数据）排最后
MAX_FULL_DAYS = 60

GROUPS = {
    "overall": (),
    "channel": ("agent_code",),
    "supplier": ("supplier_code",),
    "airline": ("ticket_carrier",),
    "channel_supplier": ("agent_code", "supplier_code"),
    "channel_airline": ("agent_code", "ticket_carrier"),
    "channel_supplier_airline": ("agent_code", "supplier_code", "ticket_carrier"),
}
LABEL = {"stat_date": "日期", "agent_code": "渠道", "supplier_code": "供应商", "ticket_carrier": "航司"}
SORT_KEYS = ("sample", "verify_fail_rate", "supplier_fail_rate", "timeout_rate", "booking_rate")
WORDING = (
    "只有汇总数据，没有错误码、接口日志或事故结论：只能说「异常主要集中在」「主要异常对象」，不能说「根因是某供应商/航司」。"
)
RATE_TOLERANCE = 0.005  # 源比率与反算值相差超过 0.5 个百分点才提示


# ---------- 纯计算 ----------

def aggregate(rows) -> dict[str, int]:
    out = dict.fromkeys(COUNT_FIELDS, 0)
    for r in rows:
        for f in COUNT_FIELDS:
            out[f] += getattr(r, f)
    return out


def ratio(n: int, d: int) -> float | None:
    return n / d if d > 0 else None


def pct_text(v: float | None) -> str:
    return NO_DATA if v is None else f"{v * 100:.2f}%"


def attention(sample: int, fail: int) -> str | None:
    """规则第四节。sample 为样本量，fail 为失败数；比率比较用整数乘法，10% 的边界不受浮点影响。
    返回 None 表示不标记问题；分母为 0 返回「无有效数据」。"""
    if sample <= 0:
        return NO_DATA
    if sample > 100 and fail * 10 >= sample:
        return "重点关注"
    if fail * 10 > sample:
        return "需要关注" if sample >= 20 else SMALL_SAMPLE
    return None


def level_text(sample: int, fail: int) -> str:
    return attention(sample, fail) or UNMARKED


def rank_key(level: str | None, contribution: float | None, fail: int) -> tuple:
    """规则第五节：重点关注 → 需要关注 → 小样本 → 其余；同级按失败贡献率降序，再按失败数降序。"""
    order = LEVEL_KEY.index(level) if level in LEVEL_KEY else len(LEVEL_KEY)
    return (order, -(contribution or 0.0), -fail)


def channel_side(a: dict) -> dict:
    t, vs, vf = a["total"], a["verify_success"], a["verify_fail"]
    return {
        "验价总数": t, "验价成功": vs, "验价失败": vf,
        "渠道校验成功率": pct_text(ratio(vs, t)),
        "渠道校验失败率": pct_text(ratio(vf, t)),
        "渠道侧判定": level_text(t, vf),
    }


def timeout_part(a: dict) -> dict:
    t, vs = a["total"], a["verify_success"]
    t5, t10 = a["timeout_5_10"], a["timeout_gt_10"]
    return {
        "超时5～10秒数量": t5, "超时超过10秒数量": t10,
        "报表超时率（超时数/总验价数）": pct_text(ratio(t5 + t10, t)),
        "校验成功内超时率（超时数/验价成功数，主要关注）": pct_text(ratio(t5 + t10, vs)),
        "成功内5～10秒超时率": pct_text(ratio(t5, vs)),
        "成功内超过10秒超时率": pct_text(ratio(t10, vs)),
    }


def supply_side(a: dict) -> dict:
    ss, sf = a["supplier_success"], a["supplier_fail"]
    n = ss + sf
    return {
        "供应校验总数": n, "供应校验成功": ss, "供应校验失败": sf,
        "供应校验成功率": pct_text(ratio(ss, n)),
        "供应校验失败率": pct_text(ratio(sf, n)),
        "供应变价数": a["supplier_changed"],
        "供应变价率": pct_text(ratio(a["supplier_changed"], ss)),
        "供应侧判定": level_text(n, sf),
    }


def booking_part(a: dict) -> dict:
    bs, bf, ub = a["booking_success"], a["booking_fail"], a["unbook"]
    done, whole = bs + bf, bs + bf + ub
    return {
        "预定成功": bs, "预定失败": bf, "未预定": ub,
        "渠道预定率": pct_text(ratio(done, whole)),
        "预定成功率": pct_text(ratio(bs, done)),
        "预定失败率": pct_text(ratio(bf, done)),
        "未预定率": pct_text(ratio(ub, whole)),
    }


def full_metrics(a: dict) -> dict:
    return {**channel_side(a), **timeout_part(a), **supply_side(a), **booking_part(a)}


def sort_value(a: dict, sort_by: str) -> float | None:
    if sort_by == "sample":
        return a["total"]
    if sort_by == "verify_fail_rate":
        return ratio(a["verify_fail"], a["total"])
    if sort_by == "supplier_fail_rate":
        return ratio(a["supplier_fail"], a["supplier_success"] + a["supplier_fail"])
    if sort_by == "timeout_rate":
        return ratio(a["timeout_5_10"] + a["timeout_gt_10"], a["total"])
    if sort_by == "booking_rate":
        return ratio(a["booking_success"] + a["booking_fail"], a["booking_success"] + a["booking_fail"] + a["unbook"])
    raise ImportValidationError(f"sort_by 只能是 {' / '.join(SORT_KEYS)}")


def group_rows(rows, fields: tuple[str, ...]) -> dict[tuple, list]:
    out: dict[tuple, list] = defaultdict(list)
    for r in rows:
        out[tuple(getattr(r, f) for f in fields)].append(r)
    return out


def _labels(fields: tuple[str, ...], key: tuple) -> dict:
    return {LABEL[f]: (str(v) if isinstance(v, date) else v) for f, v in zip(fields, key)}


def summarize(rows, group_by: str, by_day: bool = False, sort_by: str = "sample", ascending: bool = False) -> tuple[list[dict], dict]:
    """按分组汇总。返回 (分组列表, 合计)。by_day 时每个分组再按日期拆开，各日分别计算。"""
    if group_by not in GROUPS:
        raise ImportValidationError(f"group_by 只能是 {' / '.join(GROUPS)}")
    sort_value({k: 0 for k in COUNT_FIELDS}, sort_by)  # 校验 sort_by
    fields = (("stat_date",) if by_day else ()) + GROUPS[group_by]
    items = []
    for key, grp in group_rows(rows, fields).items():
        a = aggregate(grp)
        items.append((key, a))

    def order(item):
        key, a = item
        v = sort_value(a, sort_by)
        # 没有有效数据的永远排最后；升降序只影响有值的
        missing = v is None
        main = 0 if missing else (v if ascending else -v)
        return ((key[0] if by_day else 0), missing, main)

    items.sort(key=order)
    groups = [{**_labels(fields, key), **full_metrics(a)} for key, a in items]
    return groups, {"分组": "合计", **full_metrics(aggregate(rows))}


def _contribution(part: int, whole: int) -> float | None:
    return ratio(part, whole)


def diagnose(rows, agent_code: str, top_n: int = 10) -> dict:
    """规则第六节：渠道 → 供应商 / 航司 → 下钻，输出问题等级与排序依据。"""
    rows = [r for r in rows if r.agent_code == agent_code]
    if not rows:
        return {"结论": f"没有渠道 {agent_code} 在该范围的数据质量记录，无法分析。"}
    channel = aggregate(rows)
    sup_fail_all = channel["supplier_fail"]
    air_fail_all = channel["verify_fail"]

    def ranked(grp_fields, fail_of, sample_of, contrib_whole):
        out = []
        for key, grp in group_rows(rows, grp_fields).items():
            a = aggregate(grp)
            fail, sample = fail_of(a), sample_of(a)
            level = attention(sample, fail)
            contrib = _contribution(fail, contrib_whole)
            out.append((rank_key(level, contrib, fail), key, a, level, contrib))
        out.sort(key=lambda x: x[0])
        return out

    sup_n = lambda a: a["supplier_success"] + a["supplier_fail"]  # noqa: E731
    suppliers = ranked(("supplier_code",), lambda a: a["supplier_fail"], sup_n, sup_fail_all)
    airlines = ranked(("ticket_carrier",), lambda a: a["verify_fail"], lambda a: a["total"], air_fail_all)

    def sup_row(key, a, contrib):
        return {"供应商": key[0], **supply_side(a), "失败贡献率（占该渠道全部供应商供应失败）": pct_text(contrib)}

    def air_row(key, a, contrib):
        return {"航司": key[0], **channel_side(a), "失败贡献率（占该渠道全部航司验价失败）": pct_text(contrib)}

    flagged = ("重点关注", "需要关注")
    main_sup = [(k, a, c) for _, k, a, lv, c in suppliers if lv in flagged][:3]
    main_air = [(k, a, c) for _, k, a, lv, c in airlines if lv in flagged][:3]

    def drill(parent_field, parent_value, child_field, fail_of, whole_of):
        sub = [r for r in rows if getattr(r, parent_field) == parent_value]
        whole = whole_of(aggregate(sub))
        items = []
        for key, grp in group_rows(sub, (child_field,)).items():
            a = aggregate(grp)
            items.append((-fail_of(a), key, a))
        items.sort(key=lambda x: x[0])
        return [
            {LABEL[child_field]: key[0], **channel_side(a), **supply_side(a), "占上级失败比例": pct_text(_contribution(fail_of(a), whole))}
            for _, key, a in items[:5]
        ]

    # 主要异常组合：供应商 × 航司，沿用供应侧样本与失败数判定，贡献率为占该渠道全部供应失败
    combos = ranked(("supplier_code", "ticket_carrier"), lambda a: a["supplier_fail"], sup_n, sup_fail_all)
    main_combo = [
        {"供应商": k[0], "航司": k[1], **channel_side(a), **supply_side(a), "失败贡献率（占该渠道全部供应商供应失败）": pct_text(c)}
        for _, k, a, lv, c in combos if lv in flagged
    ][:3]

    return {
        "渠道": agent_code,
        "渠道层": {**channel_side(channel), **timeout_part(channel), **booking_part(channel)},
        "供应商层（按问题程度排序）": [sup_row(k, a, c) for _, k, a, _, c in suppliers[:top_n]],
        "航司层（按问题程度排序）": [air_row(k, a, c) for _, k, a, _, c in airlines[:top_n]],
        "主要异常供应商": [k[0] for k, _, _ in main_sup] or "没有达到关注等级的供应商",
        "供应商下钻（该供应商下各航司，按供应失败数）": {
            k[0]: drill("supplier_code", k[0], "ticket_carrier", lambda a: a["supplier_fail"], lambda a: a["supplier_fail"]) for k, _, _ in main_sup
        },
        "主要异常航司": [k[0] for k, _, _ in main_air] or "没有达到关注等级的航司",
        "航司下钻（该航司下各供应商，按验价失败数）": {
            k[0]: drill("ticket_carrier", k[0], "supplier_code", lambda a: a["verify_fail"], lambda a: a["verify_fail"]) for k, _, _ in main_air
        },
        "主要异常组合（供应商－航司）": main_combo or "没有达到关注等级的组合",
        "排序依据": "先重点关注，再需要关注，再小样本；同级按失败贡献率从高到低，贡献率相同按失败数从高到低。"
                  "样本量：渠道、航司为验价总数，供应商为供应校验总数；样本不足 20 的高失败率只展示、不提醒。",
        "措辞要求": WORDING,
    }


def check_rows(rows) -> list[str]:
    """规则第七节：数据校验。只提示不拦截；源比率与反算值不一致时同时给出两个数。"""
    notes: list[str] = []

    def count(label: str, pred, example=None):
        bad = [r for r in rows if pred(r)]
        if bad:
            ex = f"，例如 {example(bad[0])}" if example else ""
            notes.append(f"{len(bad)} 行{label}{ex}")

    key = lambda r: f"{r.stat_date} {r.agent_code}/{r.supplier_code}/{r.ticket_carrier}"  # noqa: E731
    count("存在负数数量", lambda r: any(getattr(r, f) < 0 for f in COUNT_FIELDS), key)
    count("总验价数不等于验价成功数加验价失败数", lambda r: r.total != r.verify_success + r.verify_fail,
          lambda r: f"{key(r)} 总数 {r.total}、成功 {r.verify_success}、失败 {r.verify_fail}")
    count("超时数量大于验价成功数", lambda r: r.timeout_5_10 + r.timeout_gt_10 > r.verify_success, key)
    count("供应商变价数大于供应商成功数", lambda r: r.supplier_changed > r.supplier_success, key)

    def rate_gap(r, field, recomputed):
        v = ratio(*recomputed)
        return v is not None and abs(float(getattr(r, field)) - v) > RATE_TOLERANCE

    def show(r, field, recomputed):
        return f"{key(r)} 源字段 {float(getattr(r, field)):.4f}、反算 {ratio(*recomputed):.4f}"

    rate_checks = {
        "timeout_rate": ("报表超时率", lambda r: (r.timeout_5_10 + r.timeout_gt_10, r.total)),
        "booking_rate": ("预定率", lambda r: (r.booking_success + r.booking_fail, r.booking_success + r.booking_fail + r.unbook)),
        "booking_success_rate": ("预定成功率", lambda r: (r.booking_success, r.booking_success + r.booking_fail)),
    }
    for field, (name, calc) in rate_checks.items():
        count(f"的源字段{name}与按数量反算的值不一致（数据口径不一致，分析以反算值为准）",
              lambda r, f=field, c=calc: rate_gap(r, f, c(r)), lambda r, f=field, c=calc: show(r, f, c(r)))

    # 预期关系，尚未用真实数据验证，仅作提示
    mismatch = [r for r in rows if r.verify_success != r.unbook + r.booking_success + r.booking_fail]
    if mismatch:
        notes.append(f"{len(mismatch)} 行验价成功数不等于未预定加预定成功加预定失败（预期关系，尚待用真实数据验证，不作为错误）")
    return notes


# ---------- 带数据库的入口 ----------

def parse_scope(date_from: str, date_to: str, range_type: str | None, period_start: str | None, period_end: str | None):
    d0, d1 = parse_date(date_from), parse_date(date_to)
    if d1 < d0:
        raise ImportValidationError("结束日期不能早于开始日期")
    rt = range_type or "full_day"
    n_days = (d1 - d0).days + 1
    if rt == "full_day":
        if n_days > MAX_FULL_DAYS:
            raise ImportValidationError(f"日期范围最多 {MAX_FULL_DAYS} 天")
        return d0, d1, None, "完整一天"
    if rt != "period":
        raise ImportValidationError("range_type 只能是 full_day 或 period")
    ps, pe = parse_time(period_start), parse_time(period_end)
    if ps is None or pe is None or ps >= pe:
        raise ImportValidationError("时间段需要提供开始和结束时间，且开始早于结束")
    if n_days > MAX_PERIOD_DAYS:
        raise ImportValidationError(f"按时间段查询最多 {MAX_PERIOD_DAYS} 天，请缩小日期范围")
    return d0, d1, (ps, pe), f"每天 {ps.strftime('%H:%M')}～{pe.strftime('%H:%M')}"


def _scope_note(d1: date, period: tuple[time, time] | None) -> dict:
    if d1 < beijing_today():
        return {}
    kind = "时间段数据是实时从接口取的" if period else "当天数据是截至查询时刻的累计值，不是完整一天"
    return {"注意": f"范围包含当天，{kind}。"}


def _filtered(rows: list[DataQualityRow], agent_code, supplier_code, ticket_carrier) -> list[DataQualityRow]:
    return [
        r for r in rows
        if (not agent_code or r.agent_code == agent_code)
        and (not supplier_code or r.supplier_code == supplier_code)
        and (not ticket_carrier or r.ticket_carrier == ticket_carrier)
    ]


def quality_summary(
    session: Session, date_from: str, date_to: str, group_by: str,
    by_day: bool = False, agent_code: str | None = None, supplier_code: str | None = None, ticket_carrier: str | None = None,
    range_type: str | None = None, period_start: str | None = None, period_end: str | None = None,
    sort_by: str | None = None, ascending: bool = False, top_n: int = 50, client=None,
) -> dict:
    d0, d1, period, scope_text = parse_scope(date_from, date_to, range_type, period_start, period_end)
    if group_by not in GROUPS:
        raise ImportValidationError(f"group_by 只能是 {' / '.join(GROUPS)}")
    rows = _filtered(load_rows(session, d0, d1, period, client=client), agent_code, supplier_code, ticket_carrier)
    if not rows:
        return {"日期范围": f"{d0} ～ {d1}", "范围": scope_text, "结论": "没有符合条件的数据质量记录。"}
    groups, total = summarize(rows, group_by, by_day, sort_by or "sample", ascending)
    shown = groups[:top_n]
    result = {
        "日期范围": f"{d0} ～ {d1}",
        "范围": scope_text,
        "分组方式": group_by + ("，按日分别计算" if by_day else "，多日先累计再算比率"),
        "合计": total,
        "组数": len(groups),
        f"分组明细（最多 {top_n} 条）": shown,
        "说明": "所有比率由数量字段求和后重新计算；分母为 0 显示「无有效数据」。样本量：渠道、航司为验价总数，供应商为供应校验总数；"
                "样本不足 20 的高失败率只展示、不提醒。渠道侧失败率与供应侧失败率是两个口径，不能混用。",
        "措辞要求": WORDING,
    }
    if len(groups) > top_n:
        result["未展示的分组数"] = len(groups) - top_n
    notes = check_rows(rows)
    if notes:
        result["数据提示"] = notes[:10]
    return {**result, **_scope_note(d1, period)}


def quality_diagnose(
    session: Session, date_from: str, date_to: str, agent_code: str,
    range_type: str | None = None, period_start: str | None = None, period_end: str | None = None,
    top_n: int = 10, client=None,
) -> dict:
    if not agent_code:
        raise ImportValidationError("请提供渠道代码")
    d0, d1, period, scope_text = parse_scope(date_from, date_to, range_type, period_start, period_end)
    rows = [r for r in load_rows(session, d0, d1, period, client=client) if r.agent_code == agent_code]
    result = {"日期范围": f"{d0} ～ {d1}", "范围": scope_text, **diagnose(rows, agent_code, top_n)}
    notes = check_rows(rows)
    if notes:
        result["数据提示"] = notes[:10]
    return {**result, **_scope_note(d1, period)}
