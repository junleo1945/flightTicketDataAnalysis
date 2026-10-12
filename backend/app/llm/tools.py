"""大模型可调用的工具。数字全部由这里的计算函数产生，模型只负责理解问题和解释结果。"""

import json
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.metrics.order_volume import DIMENSIONS as ORDER_DIMS, order_volume_anomaly
from app.metrics.profit import GROUP_DIMS, profit_anomaly, profit_summary
from app.metrics.query_ratio import l2b_anomaly

_str = {"type": "string"}
_num = {"type": "number"}
_int = {"type": "integer"}


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
            "需要查询量表已上传且范围一致。dimension：overall 整体；channel 单渠道；supplier 单供应。"
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


def _coverage(session: Session, _args: dict) -> dict:
    def rng(sql: str) -> dict:
        r = session.execute(text(sql)).mappings().one()
        return {"最早": str(r["lo"]) if r["lo"] else None, "最晚": str(r["hi"]) if r["hi"] else None}

    return {
        "订单（渠道订单，支付日期）": rng("SELECT MIN(pay_date) AS lo, MAX(pay_date) AS hi FROM tm_order"),
        "供应订单（支付日期，兜底后）": rng("SELECT MIN(CAST(pay_time_effective AS DATE)) AS lo, MAX(CAST(pay_time_effective AS DATE)) AS hi FROM supplier_order"),
        "查询量表日期": rng("SELECT MIN(biz_date) AS lo, MAX(biz_date) AS hi FROM query_stat"),
        "数据质量表日期": rng("SELECT MIN(biz_date) AS lo, MAX(biz_date) AS hi FROM supply_quality"),
    }


def _order_volume(session: Session, a: dict) -> dict:
    filters = {k: a.get(k) for k in ("channel_code", "supplier_code", "airline_code", "route")}
    return order_volume_anomaly(session, a.get("date"), a.get("x"), a.get("dimension"), filters,
                                a.get("window_end"), a.get("threshold"), a.get("top_n") or 20)


def _l2b(session: Session, a: dict) -> dict:
    return l2b_anomaly(session, a.get("date"), a.get("x"), a.get("dimension"), a.get("partner_code"),
                       a.get("range_type"), a.get("period_start"), a.get("period_end"), a.get("threshold"), a.get("top_n") or 20)


def _profit_sum(session: Session, a: dict) -> dict:
    return profit_summary(session, a.get("date_from"), a.get("date_to"), a.get("group_by"),
                          a.get("channel_code"), a.get("supplier_code"), a.get("airline_code"), a.get("route"), a.get("top_n") or 20)


def _profit_anom(session: Session, a: dict) -> dict:
    return profit_anomaly(session, a.get("date"), a.get("x"), a.get("dimension") or "channel", a.get("threshold"), a.get("top_n") or 20)


HANDLERS: dict[str, Callable[[Session, dict], dict]] = {
    "get_data_coverage": _coverage,
    "order_volume_anomaly": _order_volume,
    "l2b_anomaly": _l2b,
    "profit_summary": _profit_sum,
    "profit_anomaly": _profit_anom,
}


def run_tool(name: str, arguments: str | dict) -> dict[str, Any]:
    """执行一次工具调用，每次使用独立会话。业务错误以 {"error": ...} 返回给模型。"""
    from app.importers.common import ImportValidationError

    handler = HANDLERS.get(name)
    if handler is None:
        return {"error": f"未知工具：{name}"}
    args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
    try:
        with SessionLocal() as session:
            return handler(session, args)
    except ImportValidationError as exc:
        return {"error": str(exc)}
