"""查订比汇总的纯函数测试：渠道清单来自数据本身，不依赖佣金配置。"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

from datetime import date  # noqa: E402

import pytest  # noqa: E402

from app.importers.common import ImportValidationError  # noqa: E402
from app.llm.tools import HANDLERS, TOOL_SPECS  # noqa: E402
from app.metrics.query_ratio import summarize_groups  # noqa: E402

D1, D2 = date(2026, 10, 10), date(2026, 10, 11)


def _row(d, code, q, o, quote=0, blocked=0):
    return {"d": d, "code": code, "q": q, "quote": quote, "blocked": blocked, "o": o}


ROWS = [
    _row(D1, "AH", 100, 10, 50),  # 配了佣金的渠道
    _row(D2, "AH", 100, 10, 50),
    _row(D1, "FTVLK", 300, 25),  # 没配佣金的渠道，同样要出现
    _row(D2, "QUNAR", 50, 0, blocked=5),  # 有查询无订单
    _row(D2, "IDLE", 0, 0),  # 无业务数据
]


def test_every_channel_in_data_is_listed_regardless_of_commission():
    groups, total = summarize_groups(ROWS, "channel")
    assert [g["partner_code"] for g in groups] == ["FTVLK", "AH", "QUNAR", "IDLE"]  # 默认按查询量从高到低
    by = {g["partner_code"]: g for g in groups}
    assert by["AH"] == {"partner_code": "AH", "query": 200, "quote": 100, "quote_rate": 0.5, "blocked": 0, "order": 20, "l2b": 10.0,
                        "days_with_data": 2, "flag": None}
    assert by["QUNAR"]["blocked"] == 5 and by["IDLE"]["flag"] is None and by["IDLE"]["quote_rate"] is None
    assert by["FTVLK"]["l2b"] == 12.0 and by["FTVLK"]["days_with_data"] == 1
    assert by["QUNAR"]["flag"] == "有查询无订单" and by["QUNAR"]["l2b"] is None
    assert total["query"] == 550 and total["order"] == 45 and total["blocked"] == 5 and total["days_with_data"] == 2
    assert total["l2b"] == round(550 / 45, 2)


def test_overall_merges_all_rows_into_one_group():
    groups, total = summarize_groups(ROWS, "overall")
    assert len(groups) == 1 and groups[0]["partner_code"] == "ALL"
    assert groups[0]["query"] == total["query"] == 550


def test_sort_by_order_and_l2b():
    by_order = [g["partner_code"] for g in summarize_groups(ROWS, "channel", "order")[0]]
    assert by_order == ["FTVLK", "AH", "QUNAR", "IDLE"]
    # 表现从差到好：有查询无订单最前，其后查订比从高到低，无业务数据最后
    by_l2b = [g["partner_code"] for g in summarize_groups(ROWS, "channel", "l2b")[0]]
    assert by_l2b == ["QUNAR", "FTVLK", "AH", "IDLE"]
    with pytest.raises(ImportValidationError):
        summarize_groups(ROWS, "channel", "profit")


def test_tool_specs_and_handlers_match():
    names = {t["function"]["name"] for t in TOOL_SPECS}
    assert names == set(HANDLERS)
    assert "l2b_summary" in names
