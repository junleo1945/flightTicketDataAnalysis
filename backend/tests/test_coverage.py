import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

from datetime import date  # noqa: E402

import pytest  # noqa: E402

from app.importers.common import ImportValidationError  # noqa: E402
from app.llm.tools import HANDLERS, TOOL_SPECS  # noqa: E402
from app.metrics.coverage import describe_coverage, latest_day_looks_partial, missing_date_ranges  # noqa: E402
from app.metrics.query_ratio import build_daily_series, l2b_daily  # noqa: E402

GAP_KEY = "缺失日期（范围内没有任何订单数据，说明没上传，不是没有订单）"


def d(day: int, month: int = 10) -> date:
    return date(2026, month, day)


def test_missing_date_ranges_merges_consecutive_days():
    counts = {d(30, 9): 10, d(7): 5, d(8): 5, d(10): 1, d(12): 3}
    assert missing_date_ranges(counts) == ["2026-10-01～2026-10-06", "2026-10-09", "2026-10-11"]


def test_missing_date_ranges_without_gap_or_data():
    assert missing_date_ranges({d(1): 1, d(2): 1}) == []
    assert missing_date_ranges({}) == []


def test_latest_day_partial_when_far_below_recent_average():
    counts = {d(i): 100 for i in range(1, 8)} | {d(8): 5}
    assert latest_day_looks_partial(counts)
    assert not latest_day_looks_partial({d(i): 100 for i in range(1, 9)})


def test_describe_coverage_reports_gaps_and_partial_last_day():
    # 与线上用户 1227 的订单分布一致：10-01～10-06、10-11 缺失，10-13 只有 2 单
    counts = {d(28, 9): 637, d(29, 9): 751, d(30, 9): 977, d(7): 96, d(8): 141, d(9): 85, d(10): 41, d(12): 867, d(13): 2}
    cov = describe_coverage(counts)
    assert cov["最早"] == "2026-09-28" and cov["最晚"] == "2026-10-13"
    assert cov[GAP_KEY] == ["2026-10-01～2026-10-06", "2026-10-11"]
    assert cov["最晚一天订单数"] == 2
    assert "2026-10-13" in cov["提示"]


def test_describe_coverage_complete_data_has_no_warnings():
    cov = describe_coverage({d(i): 100 for i in range(1, 8)})
    assert GAP_KEY not in cov and "提示" not in cov


def test_describe_coverage_empty():
    assert describe_coverage({}) == {"最早": None, "最晚": None}


def test_build_daily_series_marks_missing_days_and_sums():
    series = build_daily_series(d(10), d(12), {d(10): (1000, 10), d(12): (500, 0)})
    days = series["日明细"]
    assert days[0] == {"日期": "2026-10-10", "查询量": 1000, "订单量": 10, "查订比": 100.0}
    assert days[1]["备注"] == "没有数据" and days[1]["查询量"] is None
    assert days[2]["查订比"] is None  # 订单量为 0 不计算
    assert series["合计"] == {"查询量": 1500, "订单量": 10, "查订比": 150.0, "有数据天数": 2}


def test_l2b_daily_rejects_bad_ranges_before_touching_db():
    with pytest.raises(ImportValidationError):
        l2b_daily(None, "2026-10-05", "2026-10-01", user_id=1, erp_sid="x")
    with pytest.raises(ImportValidationError):
        l2b_daily(None, "2026-09-01", "2026-10-15", user_id=1, erp_sid="x")
    with pytest.raises(ImportValidationError):
        l2b_daily(None, "2026-10-01", "2026-10-05", "bogus", user_id=1, erp_sid="x")


def test_l2b_daily_tool_is_registered():
    assert "l2b_daily" in HANDLERS
    spec = next(t["function"] for t in TOOL_SPECS if t["function"]["name"] == "l2b_daily")
    assert spec["parameters"]["required"] == ["date_from", "date_to"]
