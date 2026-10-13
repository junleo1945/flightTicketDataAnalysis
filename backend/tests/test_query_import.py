import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

from datetime import date, time  # noqa: E402

import pytest  # noqa: E402

from app.importers.common import ImportValidationError, Window  # noqa: E402
from app.importers.query import build_query_rows  # noqa: E402

WIN = Window("period", date(2026, 10, 10), time(0, 0), time(14, 0))


def test_maps_partner_type_and_counts():
    row = {"合作类型": "渠道", "合作方代码": "AH", "查询量": "13961293", "有报价量": "12797163", "订单量": "160", "航段量": "255", "被拦截次数": "0"}
    rows = build_query_rows([(2, row)], WIN, batch_id=1, user_id=1)
    assert rows[0]["partner_type"] == "channel"
    assert rows[0]["query_count"] == 13961293
    assert rows[0]["period_end"] == time(14, 0)


def test_unknown_partner_type_is_rejected():
    row = {"合作类型": "其他", "合作方代码": "X", "查询量": "1", "有报价量": "1", "订单量": "1", "航段量": "1", "被拦截次数": "0"}
    with pytest.raises(ImportValidationError):
        build_query_rows([(2, row)], WIN, batch_id=1, user_id=1)
