import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

from datetime import date, time  # noqa: E402
from decimal import Decimal  # noqa: E402

from app.importers.common import Window  # noqa: E402
from app.importers.quality import build_quality_rows  # noqa: E402

WIN = Window("full_day", date(2026, 10, 9), time(0, 0), time(23, 59, 59))

OLD_FORMAT = {  # 航司 × 供应
    "航司": "AK", "supplier_code": "DBFL", "验价总数": "4336", "验价超时5s": "261", "验价超时10s": "377",
    "验价成功数": "4041", "验价失败数": "295", "供应商成功数": "4041", "供应商失败数": "295",
    "未预定": "4295", "预定成功": "35", "预定失败": "6", "预定率": "0.0095", "超时率": "0.1471",
}


def test_old_format_without_agent_or_price_change():
    rows, _ = build_quality_rows([(2, OLD_FORMAT)], WIN, batch_id=1)
    assert rows[0]["agent_code"] == ""
    assert rows[0]["price_change"] is None
    assert rows[0]["timeout_rate"] == Decimal("0.1471")
    assert rows[0]["verify_total"] == 4336


def test_new_format_with_agent_and_price_change():
    row = {**OLD_FORMAT, "agent_code": "FLYHA", "供应商变价": "601"}
    rows, _ = build_quality_rows([(2, row)], WIN, batch_id=1)
    assert rows[0]["agent_code"] == "FLYHA"
    assert rows[0]["price_change"] == 601


def test_rows_missing_airline_are_skipped_not_fatal():
    blank = {**OLD_FORMAT, "航司": None}
    rows, skipped = build_quality_rows([(2, OLD_FORMAT), (3, blank)], WIN, batch_id=1)
    assert len(rows) == 1
    assert skipped == [3]
