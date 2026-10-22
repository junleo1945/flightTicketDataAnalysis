import json
import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

from datetime import date, datetime, time  # noqa: E402
from decimal import Decimal  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.auth.erp import ErpUpstreamError  # noqa: E402
from app.erp.data_quality import DataQualityClient  # noqa: E402
from app.erp.quality_cache import period_bounds  # noqa: E402


def _item(**over):
    base = {
        "stat_date": "2026-10-12", "agent_code": "JDBP", "supplier_code": "DBFL", "ticket_carrier": "UA",
        "total": 6, "timeout_5_10": 0, "timeout_gt_10": 0, "verify_success": 6, "verify_fail": 0,
        "supplier_success": 6, "supplier_fail": 0, "supplier_changed": 0, "unbook": 6,
        "booking_success": 0, "booking_fail": 0, "booking_rate": 0, "booking_success_rate": 0, "timeout_rate": 0,
    }
    return {**base, **over}


def _client(handler):
    return DataQualityClient("http://dq.internal/", transport=httpx.MockTransport(handler))


def _ok(items, total=None):
    return httpx.Response(200, json={"code": 0, "message": "success", "data": {"items": items, "total": len(items) if total is None else total}})


def test_fetch_day_sends_full_day_range_and_no_auth_headers():
    seen = []

    def handler(req):
        seen.append(req)
        return _ok([_item(booking_rate=0.6667)])

    rows = _client(handler).fetch_day(date(2026, 10, 12))
    body = json.loads(seen[0].content)
    assert body == {"start_time": "2026-10-12 00:00:00", "end_time": "2026-10-12 23:59:59", "page_no": 1, "page_size": 500}
    assert str(seen[0].url) == "http://dq.internal/flight/data-quality/query"
    assert "cookie" not in seen[0].headers and "authorization" not in seen[0].headers
    assert seen[0].headers["cloud-trace-id"]
    assert rows[0].stat_date == date(2026, 10, 12) and rows[0].total == 6 and rows[0].booking_rate == Decimal("0.6667")


def test_fetch_range_passes_hourly_range_through():
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return _ok([])

    _client(handler).fetch_range(datetime(2026, 10, 12, 10, 0), datetime(2026, 10, 12, 12, 0))
    assert seen[0]["start_time"] == "2026-10-12 10:00:00" and seen[0]["end_time"] == "2026-10-12 12:00:00"


def test_pagination_stops_when_total_reached():
    pages = []

    def handler(req):
        page = json.loads(req.content)["page_no"]
        pages.append(page)
        return _ok([_item(agent_code=f"A{page}{i}") for i in range(2)], total=4)

    assert len(_client(handler).fetch_day(date(2026, 10, 12))) == 4
    assert pages == [1, 2]


def test_duplicate_key_keeps_last_record():
    rows = _client(lambda req: _ok([_item(total=1), _item(total=9)])).fetch_day(date(2026, 10, 12))
    assert len(rows) == 1 and rows[0].total == 9


def test_missing_numbers_default_to_zero():
    rows = _client(lambda req: _ok([{"stat_date": "2026-10-12", "agent_code": "A", "supplier_code": "S", "ticket_carrier": "CA"}])).fetch_day(date(2026, 10, 12))
    assert rows[0].total == 0 and rows[0].booking_rate == Decimal("0")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"code": 1, "message": "参数错误"}),
        httpx.Response(200, json={"code": 0, "data": []}),
        httpx.Response(200, json=[1, 2]),
    ],
)
def test_bad_responses_are_upstream_errors(response):
    with pytest.raises(ErpUpstreamError):
        _client(lambda req: response).fetch_day(date(2026, 10, 12))


def test_business_failure_message_is_kept():
    with pytest.raises(ErpUpstreamError, match="参数错误"):
        _client(lambda req: httpx.Response(200, json={"code": 1, "message": "参数错误"})).fetch_day(date(2026, 10, 12))


def test_network_error_is_upstream_error():
    def handler(req):
        raise httpx.ConnectError("down", request=req)

    with pytest.raises(ErpUpstreamError, match="请求失败"):
        _client(handler).fetch_day(date(2026, 10, 12))


def test_bad_date_in_item_is_upstream_error():
    with pytest.raises(ErpUpstreamError, match="日期"):
        _client(lambda req: _ok([_item(stat_date="oops")])).fetch_day(date(2026, 10, 12))


def test_pagination_over_limit_is_error(monkeypatch):
    monkeypatch.setattr("app.erp.data_quality.MAX_PAGES", 2)
    with pytest.raises(ErpUpstreamError, match="分页"):
        _client(lambda req: _ok([_item()], total=99)).fetch_day(date(2026, 10, 12))


def test_start_after_end_is_rejected():
    with pytest.raises(ErpUpstreamError):
        _client(lambda req: _ok([])).fetch_range(datetime(2026, 10, 12, 12), datetime(2026, 10, 12, 10))


def test_period_bounds_use_exact_end_except_end_of_day():
    d = date(2026, 10, 12)
    assert period_bounds(d, time(10, 0), time(12, 0)) == (datetime(2026, 10, 12, 10, 0, 0), datetime(2026, 10, 12, 12, 0, 0))
    assert period_bounds(d, time(22, 0), time(23, 59))[1] == datetime(2026, 10, 12, 23, 59, 59)
