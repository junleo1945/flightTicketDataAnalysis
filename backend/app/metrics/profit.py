"""场景 3：利润。规则见方案 3.5（已确认）。

- 只统计渠道订单 订单状态 = 出票完成 的订单。
- 销售额取渠道订单金额（一单一次）；成本取同一途灵订单下 出票完成 的供应订单之和。
- 无佣金：利润 = 销售额 − 成本（CNY、USD 各算各的）。
- 佣金率：利润 = 销售额 × (1 − 佣金率) − 成本。
- FMOVE 每单 0.2 USD：先用订单原币种计算（订单金额 − 成本折回原币 − 0.2 USD 折回原币），再分别折算成 CNY、USD。
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.importers.common import ImportValidationError
from app.metrics.common import STATUS_DONE, history_range, parse_date, parse_threshold, parse_x, pct

SUPPLIER_EXPR = "s.supplier_code"


@dataclass(frozen=True)
class Rule:
    rule_type: str  # percent / fixed_usd
    value: Decimal


@dataclass
class OrderFacts:
    tm_order_no: str
    channel_code: str
    day: date
    currency: str | None
    total_price: Decimal
    sales_cny: Decimal
    sales_usd: Decimal
    rate_cny: Decimal | None
    rate_usd: Decimal | None
    suppliers: list[tuple[str, Decimal, Decimal]] = field(default_factory=list)  # (供应商代码, 成本CNY, 成本USD)
    airlines: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)


def order_profit(f: OrderFacts, rule: Rule | None) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """返回 (利润CNY, 利润USD, 成本CNY, 成本USD)。纯函数，便于单元测试。"""
    cost_cny = sum((c for _, c, _ in f.suppliers), Decimal("0"))
    cost_usd = sum((u for _, _, u in f.suppliers), Decimal("0"))
    if rule is None:
        return f.sales_cny - cost_cny, f.sales_usd - cost_usd, cost_cny, cost_usd
    if rule.rule_type == "percent":
        keep = 1 - rule.value
        return f.sales_cny * keep - cost_cny, f.sales_usd * keep - cost_usd, cost_cny, cost_usd
    if rule.rule_type == "fixed_usd":
        if not f.rate_cny or not f.rate_usd:
            raise ImportValidationError(f"订单 {f.tm_order_no} 缺少汇率，无法计算固定扣费利润")
        cost_orig = cost_cny / f.rate_cny  # 成本折回订单原币种
        fee_orig = rule.value / f.rate_usd  # 0.2 USD 折回订单原币种
        profit_orig = f.total_price - cost_orig - fee_orig
        return profit_orig * f.rate_cny, profit_orig * f.rate_usd, cost_cny, cost_usd
    raise ImportValidationError(f"未知的佣金规则类型：{rule.rule_type}")


def allocations(f: OrderFacts, dim: str) -> list[tuple[str, Decimal, Decimal, int]]:
    """把一个订单分摊到分组上。返回 [(分组值, CNY份额, USD份额, 计数)]。
    channel：整单归渠道；airline / route：多值时按个数平均分摊；supplier：按各供应商成本占比分摊。
    """
    one = Decimal("1")
    if dim == "channel":
        return [(f.channel_code, one, one, 1)]
    if dim in ("airline", "route"):
        items = list(dict.fromkeys(f.airlines if dim == "airline" else f.routes))
        if not items:
            return [("(无)", one, one, 1)]
        share = one / len(items)
        return [(k, share, share, 1) for k in items]
    if dim == "supplier":
        if not f.suppliers:
            return [("(无供应订单)", one, one, 1)]
        total_c = sum((c for _, c, _ in f.suppliers), Decimal("0"))
        total_u = sum((u for _, _, u in f.suppliers), Decimal("0"))
        n = len(f.suppliers)
        acc: dict[str, list] = {}
        for code, c, u in f.suppliers:
            fc = c / total_c if total_c else one / n
            fu = u / total_u if total_u else one / n
            a = acc.setdefault(code, [Decimal("0"), Decimal("0")])
            a[0] += fc
            a[1] += fu
        return [(k, v[0], v[1], 1) for k, v in acc.items()]
    raise ImportValidationError(f"利润不支持的分组维度：{dim}")


GROUP_DIMS = ("channel", "supplier", "airline", "route")


def load_orders(session: Session, d0: date, d1: date, filters: dict, user_id: int) -> list[OrderFacts]:
    params = {"d0": d0, "d1": d1, "done": STATUS_DONE, "uid": user_id}
    orders = session.execute(
        text(
            """SELECT o.tm_order_no, o.channel_code, o.pay_date AS day, o.currency, o.total_price,
                      o.total_cny, o.total_usd, o.rate_cny, o.rate_usd
               FROM tm_order o
               WHERE o.user_id = :uid AND o.order_status = :done AND o.pay_date BETWEEN :d0 AND :d1
                 AND (CAST(:ch AS TEXT) IS NULL OR o.channel_code = :ch)"""
        ),
        {**params, "ch": filters.get("channel_code") or None},
    ).mappings().all()
    facts = {
        o["tm_order_no"]: OrderFacts(
            tm_order_no=o["tm_order_no"], channel_code=o["channel_code"], day=o["day"], currency=o["currency"],
            total_price=o["total_price"] or Decimal("0"),
            sales_cny=o["total_cny"] or Decimal("0"), sales_usd=o["total_usd"] or Decimal("0"),
            rate_cny=o["rate_cny"], rate_usd=o["rate_usd"],
        )
        for o in orders
    }
    if not facts:
        return []

    sup = session.execute(
        text(
            f"""SELECT s.tm_order_no, {SUPPLIER_EXPR} AS code, s.supplier_total_cny AS c, s.supplier_total_usd AS u
                FROM supplier_order s JOIN tm_order o ON o.tm_order_no = s.tm_order_no AND o.user_id = s.user_id
                WHERE s.user_id = :uid AND s.status = :done AND o.order_status = :done AND o.pay_date BETWEEN :d0 AND :d1"""
        ),
        params,
    ).mappings().all()
    for r in sup:
        if r["tm_order_no"] in facts:
            facts[r["tm_order_no"]].suppliers.append((r["code"], r["c"] or Decimal("0"), r["u"] or Decimal("0")))

    for r in session.execute(
        text(
            """SELECT a.tm_order_no, a.airline_code FROM order_airline a
               JOIN tm_order o ON o.tm_order_no = a.tm_order_no AND o.user_id = a.user_id
               WHERE a.user_id = :uid AND o.order_status = :done AND o.pay_date BETWEEN :d0 AND :d1"""
        ),
        params,
    ).mappings():
        if r["tm_order_no"] in facts:
            facts[r["tm_order_no"]].airlines.append(r["airline_code"])
    for r in session.execute(
        text(
            """SELECT r.tm_order_no, r.route FROM order_route r
               JOIN tm_order o ON o.tm_order_no = r.tm_order_no AND o.user_id = r.user_id
               WHERE r.user_id = :uid AND o.order_status = :done AND o.pay_date BETWEEN :d0 AND :d1"""
        ),
        params,
    ).mappings():
        if r["tm_order_no"] in facts:
            facts[r["tm_order_no"]].routes.append(r["route"])

    result = list(facts.values())
    # 按订单整单过滤（航司、航线、供应商）
    if filters.get("airline_code"):
        result = [f for f in result if filters["airline_code"] in f.airlines]
    if filters.get("route"):
        result = [f for f in result if filters["route"] in f.routes]
    if filters.get("supplier_code"):
        result = [f for f in result if any(c == filters["supplier_code"] for c, _, _ in f.suppliers)]
    return result


def load_rules(session: Session, day_from: date, day_to: date) -> dict[str, Rule]:
    rows = session.execute(
        text(
            """SELECT channel_code, rule_type, value, effective_from FROM commission_rule
               WHERE effective_from <= :d1 AND (effective_to IS NULL OR effective_to >= :d0)
               ORDER BY effective_from"""
        ),
        {"d0": day_from, "d1": day_to},
    ).mappings().all()
    return {r["channel_code"]: Rule(r["rule_type"], r["value"]) for r in rows}  # 同一渠道取生效日最晚的一条


def profit_rows(session: Session, d0: date, d1: date, dim: str, filters: dict, user_id: int) -> list[dict]:
    """逐订单计算利润，再按分组维度分摊汇总，结果按（日期、分组）返回。"""
    rules = load_rules(session, d0, d1)
    out: dict[tuple, dict] = {}
    for f in load_orders(session, d0, d1, filters, user_id):
        pc, pu, cc, cu = order_profit(f, rules.get(f.channel_code))
        for key, fc, fu, cnt in allocations(f, dim):
            r = out.setdefault((f.day, key), {
                "day": f.day, "key": key, "orders": 0, "sales_cny": Decimal("0"), "sales_usd": Decimal("0"),
                "cost_cny": Decimal("0"), "cost_usd": Decimal("0"), "profit_cny": Decimal("0"), "profit_usd": Decimal("0"),
                "loss_orders": 0,
            })
            r["orders"] += cnt
            r["sales_cny"] += f.sales_cny * fc
            r["sales_usd"] += f.sales_usd * fu
            r["cost_cny"] += cc * fc
            r["cost_usd"] += cu * fu
            r["profit_cny"] += pc * fc
            r["profit_usd"] += pu * fu
            r["loss_orders"] += 1 if pu * fu < 0 else 0
    return list(out.values())


def _money(v: Decimal) -> float:
    return float(round(v, 2))


def profit_summary(session: Session, date_from: str, date_to: str, group_by: str = "channel",
                   channel_code=None, supplier_code=None, airline_code=None, route=None, top_n: int = 20, *, user_id: int) -> dict:
    d0, d1 = parse_date(date_from), parse_date(date_to)
    if d1 < d0:
        raise ImportValidationError("结束日期不能早于开始日期")
    if group_by not in GROUP_DIMS:
        raise ImportValidationError(f"group_by 只能是 {' / '.join(GROUP_DIMS)}")
    filters = {"channel_code": channel_code, "supplier_code": supplier_code, "airline_code": airline_code, "route": route}
    filters = {k: v for k, v in filters.items() if v}

    orders = load_orders(session, d0, d1, filters, user_id)
    rules = load_rules(session, d0, d1)
    totals = [order_profit(f, rules.get(f.channel_code)) for f in orders]
    total_profit_cny = sum((t[0] for t in totals), Decimal("0"))
    total_profit_usd = sum((t[1] for t in totals), Decimal("0"))
    groups = {}
    for r in profit_rows(session, d0, d1, group_by, filters, user_id):
        g = groups.setdefault(r["key"], {k: Decimal("0") if isinstance(v, Decimal) else 0 for k, v in r.items() if k not in ("day", "key")})
        for k in g:
            g[k] += r[k]
    items = []
    for key, g in groups.items():
        avg = g["profit_usd"] / g["orders"] if g["orders"] else Decimal("0")
        items.append({
            "group": key,
            "orders": float(round(g["orders"], 2)),
            "sales_cny": _money(g["sales_cny"]), "sales_usd": _money(g["sales_usd"]),
            "cost_cny": _money(g["cost_cny"]), "cost_usd": _money(g["cost_usd"]),
            "profit_cny": _money(g["profit_cny"]), "profit_usd": _money(g["profit_usd"]),
            "avg_profit_usd": _money(avg),
            "loss_orders": g["loss_orders"],
        })
    items.sort(key=lambda i: i["profit_usd"])
    return {
        "日期范围": f"{d0} ～ {d1}",
        "分组": group_by,
        "统计口径": "渠道订单（采购维度 出票完成），销售额与成本按规则计算，CNY 与 USD 分别计算；多值分组（航司/航线/供应商）按份额分摊，合计不重复计数订单总量",
        "订单数": len(orders),
        "总销售额": {"CNY": _money(sum((f.sales_cny for f in orders), Decimal("0"))), "USD": _money(sum((f.sales_usd for f in orders), Decimal("0")))},
        "总利润": {"CNY": _money(total_profit_cny), "USD": _money(total_profit_usd)},
        "亏损订单数": sum(1 for t in totals if t[1] < 0),
        "分组数": len(items),
        "分组明细（利润 USD 从低到高）": items[:top_n],
        "佣金说明": "全部渠道都计入；配置了佣金规则的渠道按规则扣减，未配置的渠道不扣佣金（利润 = 销售额 − 成本）。",
    }


def profit_anomaly(session: Session, date_str: str, x, dimension: str = "channel", threshold=None, top_n: int = 20, *, user_id: int) -> dict:
    """场景 3：利润异常。当日利润 vs 前 x 天日均利润，并拆解为「订单量」与「单均利润」的变化。"""
    if dimension not in ("channel", "supplier", "airline", "route"):
        raise ImportValidationError("利润异常的 dimension 只能是 channel / supplier / airline / route")
    day = parse_date(date_str)
    n = parse_x(x)
    thr = parse_threshold(threshold)
    h0, h1 = history_range(day, n)

    rows = profit_rows(session, h0, day, dimension, {}, user_id)
    valid_days = {r["day"] for r in rows if r["day"] < day and r["orders"] > 0}
    by_key: dict[str, dict] = {}
    for r in rows:
        g = by_key.setdefault(r["key"], {"cur": None, "hist": []})
        if r["day"] == day:
            g["cur"] = r
        elif r["day"] in valid_days:
            g["hist"].append(r)

    items = []
    for key, g in by_key.items():
        cur = g["cur"] or {"orders": 0, "profit_usd": Decimal("0"), "profit_cny": Decimal("0")}
        hist_profit = sum((h["profit_usd"] for h in g["hist"]), Decimal("0"))
        hist_orders = sum((h["orders"] for h in g["hist"]), Decimal("0"))
        base_profit = hist_profit / len(valid_days) if valid_days else None
        base_orders = hist_orders / len(valid_days) if valid_days else None
        base_avg = (hist_profit / hist_orders) if hist_orders else None
        cur_avg = (cur["profit_usd"] / cur["orders"]) if cur["orders"] else None
        change = None
        if base_profit is not None and base_profit != 0:
            change = float((cur["profit_usd"] - base_profit) / abs(base_profit))
        items.append({
            "group": key,
            "current_profit_usd": _money(cur["profit_usd"]),
            "current_profit_cny": _money(cur["profit_cny"]),
            "baseline_profit_usd_daily_avg": _money(base_profit) if base_profit is not None else None,
            "profit_change_rate": change,
            "profit_change_text": pct(change),
            "current_orders": float(cur["orders"]),
            "baseline_orders_daily_avg": float(round(base_orders, 2)) if base_orders is not None else None,
            "current_avg_profit_usd": _money(cur_avg) if cur_avg is not None else None,
            "baseline_avg_profit_usd": _money(base_avg) if base_avg is not None else None,
            "anomaly": change is not None and change < thr,
        })
    anomalies = sorted((i for i in items if i["anomaly"]), key=lambda i: i["profit_change_rate"])
    return {
        "日期": str(day),
        "维度": dimension,
        "历史窗口": f"{h0}～{h1}",
        "有效历史天数": len(valid_days),
        "x": n,
        "阈值（利润变化率，相对历史日均利润的绝对值）": thr,
        "异常组数": len(anomalies),
        "异常明细（变化率从低到高）": anomalies[:top_n],
        "说明": "利润变化可拆解为：订单量变化（current_orders vs baseline_orders_daily_avg）与单均利润变化（current_avg_profit_usd vs baseline_avg_profit_usd）。",
    }


def _order_rows(facts: list[OrderFacts], rules: dict[str, Rule], only_loss: bool) -> list[dict]:
    """逐单生成利润明细（按利润 USD 从低到高）。纯函数，便于单元测试。"""
    rows = []
    for f in facts:
        pc, pu, cc, cu = order_profit(f, rules.get(f.channel_code))
        if only_loss and pu >= 0:
            continue
        rows.append({
            "途灵订单号": f.tm_order_no,
            "支付日期": str(f.day),
            "渠道": f.channel_code,
            "航司": ",".join(dict.fromkeys(f.airlines)) or "(无)",
            "航线": ",".join(dict.fromkeys(f.routes)) or "(无)",
            "供应商": ",".join(dict.fromkeys(c for c, _, _ in f.suppliers)) or "(无供应订单)",
            "销售额": {"CNY": _money(f.sales_cny), "USD": _money(f.sales_usd)},
            "成本": {"CNY": _money(cc), "USD": _money(cu)},
            "利润": {"CNY": _money(pc), "USD": _money(pu)},
            "_sort": pu,
        })
    rows.sort(key=lambda r: r["_sort"])
    for r in rows:
        del r["_sort"]
    return rows


def profit_orders(session: Session, date_from: str, date_to: str, channel_code=None, supplier_code=None,
                  airline_code=None, route=None, only_loss: bool = True, top_n: int = 50, *, user_id: int) -> dict:
    """逐单利润明细：列出每一笔渠道订单（途灵订单号）的销售额、成本与利润，口径与利润汇总一致。"""
    d0, d1 = parse_date(date_from), parse_date(date_to)
    if d1 < d0:
        raise ImportValidationError("结束日期不能早于开始日期")
    filters = {"channel_code": channel_code, "supplier_code": supplier_code, "airline_code": airline_code, "route": route}
    filters = {k: v for k, v in filters.items() if v}

    facts = load_orders(session, d0, d1, filters, user_id)
    rows = _order_rows(facts, load_rules(session, d0, d1), only_loss)
    return {
        "日期范围": f"{d0} ～ {d1}",
        "统计口径": "渠道订单（采购维度 出票完成），销售额与成本按规则计算，与利润汇总口径一致",
        "筛选": "仅亏损订单（利润 USD < 0）" if only_loss else "全部订单",
        "符合条件的订单数": len(rows),
        f"订单明细（利润 USD 从低到高，最多 {top_n} 条）": rows[:top_n],
    }
