"""大模型可调用的工具。数字全部由这里的计算函数产生，模型只负责理解问题和解释结果。"""

import json
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.scope import visible_user_ids
from app.metrics.order_volume import DIMENSIONS as ORDER_DIMS, order_volume_anomaly
from app.metrics.profit import GROUP_DIMS, profit_anomaly, profit_orders, profit_summary
from app.metrics.query_ratio import l2b_anomaly

_str = {"type": "string"}
_num = {"type": "number"}
_int = {"type": "integer"}


@dataclass(frozen=True)
class ToolContext:
    """当前提问人的身份：查询量等按人隔离的数据据此过滤，ERP 拉取也用其会话。"""

    user_id: int
    erp_sid: str


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


TOOL_SPECS: list[dict] = [
    {"type": "function", "function": {
        "name": "get_data_coverage",
        "description": "查看已导入数据的日期范围，判断某个问题能否回答。",
        "parameters": _obj({}, []),
    }},
    {"type": "function", "function": {
        "name": "order_volume_anomaly",
        "description": (
            "订单量异常（场景1）：当前窗口（当天 00:00～window_end）的订单量，与前 x 天同时段日均订单量对比，"
            "下降超过阈值判定异常。dimension 说明：overall 整体；channel 单渠道；channel_airline 单渠道单航司；"
            "channel_airline_route 单渠道单航司单航线；supplier 单供应；supplier_airline 单供应单航司；"
            "supplier_airline_route 单供应单航司单航线。filters 可按渠道/供应/航司/航线过滤。"
        ),
        "parameters": _obj({
            "date": {**_str, "description": "业务日期 YYYY-MM-DD（北京时间）"},
            "x": {**_int, "description": "历史对比天数，1～60"},
            "dimension": {"type": "string", "enum": list(ORDER_DIMS)},
            "channel_code": _str,
            "supplier_code": _str,
            "airline_code": _str,
            "route": {**_str, "description": "航线，如 KUL-HRB"},
            "window_end": {**_str, "description": "当前窗口截止时间 HH:MM，不填则为当天最晚一笔订单的时间"},
            "threshold": {**_num, "description": "下降阈值，负数，默认 -0.1"},
            "top_n": _int,
        }, ["date", "x", "dimension"]),
    }},
    {"type": "function", "function": {
        "name": "l2b_anomaly",
        "description": (
            "查订比异常（场景2）：查订比 = 查询量 / 订单量。当前查订比与前 x 天汇总查订比基线对比，下降超过阈值判定异常。"
            "查询量数据：完整一天由 ERP 接口按日获取（历史整天会缓存，当天实时获取）；时间段范围需要上传查询量表。"
            "dimension：overall 整体；channel 单渠道；supplier 单供应。"
        ),
        "parameters": _obj({
            "date": _str,
            "x": _int,
            "dimension": {"type": "string", "enum": ["overall", "channel", "supplier"]},
            "partner_code": {**_str, "description": "合作方代码，不填则返回全部"},
            "range_type": {"type": "string", "enum": ["full_day", "period"], "description": "默认 full_day"},
            "period_start": {**_str, "description": "时间段开始 HH:MM，range_type=period 时必填"},
            "period_end": {**_str, "description": "时间段结束 HH:MM，range_type=period 时必填"},
            "threshold": _num,
            "top_n": _int,
        }, ["date", "x", "dimension"]),
    }},
    {"type": "function", "function": {
        "name": "profit_summary",
        "description": (
            "利润汇总（场景3）：指定日期范围内，按分组维度汇总销售额、成本、利润，CNY 与 USD 分别计算。"
            "只统计出票完成的渠道订单。group_by 可选 channel / supplier / airline / route。"
        ),
        "parameters": _obj({
            "date_from": _str,
            "date_to": _str,
            "group_by": {"type": "string", "enum": list(GROUP_DIMS)},
            "channel_code": _str,
            "supplier_code": _str,
            "airline_code": _str,
            "route": _str,
            "top_n": _int,
        }, ["date_from", "date_to", "group_by"]),
    }},
    {"type": "function", "function": {
        "name": "profit_orders",
        "description": (
            "利润订单明细：列出指定日期范围内每一笔渠道订单（途灵订单号）的销售额、成本与利润，口径与利润汇总一致。"
            "用于回答「亏损订单有哪些」「某航司的订单号」等需要具体订单号的问题。"
            "only_loss 默认 true，只列利润为负的订单，按亏损从大到小排序。航司、航线、渠道、供应商可过滤。"
        ),
        "parameters": _obj({
            "date_from": _str,
            "date_to": _str,
            "channel_code": _str,
            "supplier_code": _str,
            "airline_code": {**_str, "description": "航司二字码，如 FD"},
            "route": {**_str, "description": "航线，如 KUL-HRB"},
            "only_loss": {"type": "boolean", "description": "是否只列亏损订单，默认 true"},
            "top_n": {**_int, "description": "最多返回条数，默认 50"},
        }, ["date_from", "date_to"]),
    }},
    {"type": "function", "function": {
        "name": "commission_rules",
        "description": (
            "查看当前佣金规则配置：每个渠道的佣金方式、数值和生效时间。"
            "percent 表示按销售额的百分比扣减（利润 = 销售额 × (1 − 佣金率) − 成本）；"
            "fixed_usd 表示每单固定扣费（单位 USD）。不填渠道则返回全部。"
        ),
        "parameters": _obj({
            "channel_code": _str,
        }, []),
    }},
    {"type": "function", "function": {
        "name": "profit_anomaly",
        "description": (
            "利润异常（场景3）：当日利润与前 x 天日均利润对比（以 USD 判断），并拆解为订单量变化与单均利润变化。"
        ),
        "parameters": _obj({
            "date": _str,
            "x": _int,
            "dimension": {"type": "string", "enum": ["channel", "supplier", "airline", "route"]},
            "threshold": _num,
            "top_n": _int,
        }, ["date", "x"]),
    }},
]


def describe_rule(rule_type: str, value: Any) -> str:
    """把佣金配置翻译成业务语言，例如 percent 0.02 → 佣金 2%。"""
    v = float(value)
    if rule_type == "percent":
        return f"佣金 {v * 100:g}%"
    if rule_type == "fixed_usd":
        return f"每单固定扣费 {v:g} USD"
    return f"未知规则类型 {rule_type}"


def _commission_rules(session: Session, a: dict, ctx: ToolContext) -> dict:
    sql = "SELECT channel_code, rule_type, value, effective_from, effective_to FROM commission_rule"
    params: dict[str, Any] = {}
    if a.get("channel_code"):
        sql += " WHERE channel_code = :c"
        params["c"] = a["channel_code"]
    rows = session.execute(text(sql + " ORDER BY channel_code, effective_from"), params).mappings().all()
    return {
        "佣金规则": [
            {
                "渠道": r["channel_code"],
                "规则": describe_rule(r["rule_type"], r["value"]),
                "生效日期": str(r["effective_from"]),
                "失效日期": str(r["effective_to"]) if r["effective_to"] else "长期有效",
            }
            for r in rows
        ],
        "说明": "未列出的渠道没有佣金，利润 = 销售额 − 成本。",
    }


def _coverage(session: Session, _args: dict, ctx: ToolContext) -> dict:
    # 统计当前用户自己及被授权用户的数据范围（与指标查询的可见范围一致）
    params = {"uids": visible_user_ids(session, int(ctx.user_id))}
    def rng(sql: str) -> dict:
        r = session.execute(text(sql), params).mappings().one()
        return {"最早": str(r["lo"]) if r["lo"] else None, "最晚": str(r["hi"]) if r["hi"] else None}

    return {
        "订单（渠道订单，支付日期）": rng("SELECT MIN(pay_date) AS lo, MAX(pay_date) AS hi FROM tm_order WHERE user_id = ANY(:uids)"),
        "供应订单（支付日期，兜底后）": rng("SELECT MIN(CAST(pay_time_effective AS DATE)) AS lo, MAX(CAST(pay_time_effective AS DATE)) AS hi FROM supplier_order WHERE user_id = ANY(:uids)"),
        "查询量表日期（Excel 上传）": rng("SELECT MIN(biz_date) AS lo, MAX(biz_date) AS hi FROM query_stat WHERE source = 'excel' AND user_id = ANY(:uids)"),
        "查询量（完整一天，ERP 接口）": "按需获取，可查询任意历史日期；当天为截至查询时的实时累计",
        "数据质量表日期": rng("SELECT MIN(biz_date) AS lo, MAX(biz_date) AS hi FROM supply_quality WHERE user_id = ANY(:uids)"),
    }


def _order_volume(session: Session, a: dict, ctx: ToolContext) -> dict:
    filters = {k: a.get(k) for k in ("channel_code", "supplier_code", "airline_code", "route")}
    return order_volume_anomaly(session, a.get("date"), a.get("x"), a.get("dimension"), filters,
                                a.get("window_end"), a.get("threshold"), a.get("top_n") or 20, user_id=ctx.user_id)


def _l2b(session: Session, a: dict, ctx: ToolContext) -> dict:
    return l2b_anomaly(session, a.get("date"), a.get("x"), a.get("dimension"), a.get("partner_code"),
                       a.get("range_type"), a.get("period_start"), a.get("period_end"), a.get("threshold"), a.get("top_n") or 20,
                       user_id=ctx.user_id, erp_sid=ctx.erp_sid)


def _profit_sum(session: Session, a: dict, ctx: ToolContext) -> dict:
    return profit_summary(session, a.get("date_from"), a.get("date_to"), a.get("group_by"),
                          a.get("channel_code"), a.get("supplier_code"), a.get("airline_code"), a.get("route"), a.get("top_n") or 20,
                          user_id=ctx.user_id)


def _profit_anom(session: Session, a: dict, ctx: ToolContext) -> dict:
    return profit_anomaly(session, a.get("date"), a.get("x"), a.get("dimension") or "channel", a.get("threshold"), a.get("top_n") or 20,
                          user_id=ctx.user_id)


def _profit_ord(session: Session, a: dict, ctx: ToolContext) -> dict:
    only_loss = a.get("only_loss")
    return profit_orders(session, a.get("date_from"), a.get("date_to"), a.get("channel_code"), a.get("supplier_code"),
                         a.get("airline_code"), a.get("route"), True if only_loss is None else bool(only_loss),
                         a.get("top_n") or 50, user_id=ctx.user_id)


HANDLERS: dict[str, Callable[[Session, dict], dict]] = {
    "get_data_coverage": _coverage,
    "order_volume_anomaly": _order_volume,
    "l2b_anomaly": _l2b,
    "profit_summary": _profit_sum,
    "profit_anomaly": _profit_anom,
    "profit_orders": _profit_ord,
    "commission_rules": _commission_rules,
}


def run_tool(name: str, arguments: str | dict, ctx: ToolContext) -> dict[str, Any]:
    """执行一次工具调用，每次使用独立会话。业务错误以 {"error": ...} 返回给模型。"""
    from app.importers.common import ImportValidationError

    handler = HANDLERS.get(name)
    if handler is None:
        return {"error": f"未知工具：{name}"}
    args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
    try:
        with SessionLocal() as session:
            return handler(session, args, ctx)
    except ImportValidationError as exc:
        return {"error": str(exc)}
