import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

from datetime import date  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.auth.erp import ErpAuthError, ErpUpstreamError  # noqa: E402
from app.erp.look2book import Look2BookClient  # noqa: E402

BASE = "https://erp.test"


def _login_ok(req: httpx.Request) -> httpx.Response | None:
    if req.url.path == "/api/login":
        return httpx.Response(200, json={"Success": True}, headers={"set-cookie": "tm_sso_sid=svc-sid; Path=/"})
    if req.url.path == "/user/api/get":
        return httpx.Response(200, json={"Success": True, "Data": {"UserId": 1, "UserName": "svc", "ExpireDuration": 3600}})
    return None


def _item(ctype, code, **kw):
    base = {"CType": ctype, "Code": code, "LookNum": 10, "LookSuccNum": 6, "BookNum": 2, "SegmentNum": 3, "InterceptNum": 1}
    base.update(kw)
    return base


def _client(handler) -> Look2BookClient:
    def transport_handler(req: httpx.Request) -> httpx.Response:
        auth = _login_ok(req)
        return auth if auth is not None else handler(req)

    return Look2BookClient(BASE, "svc", "pw", transport=httpx.MockTransport(transport_handler))


def test_fetch_day_maps_fields_and_sends_full_day_range():
    seen = []

    def handler(req):
        seen.append(req)
        import json

        body = json.loads(req.content)
        assert req.url.path == "/flight/config/look2book/records"
        assert body["Start"] == body["End"] == "2026-10-10" and body["CType"] == 0
        return httpx.Response(200, json={"data": {"List": [_item(1, "QUNAR"), _item(2, "AH", LookNum=5)], "Total": 2}})

    rows = _client(handler).fetch_day(date(2026, 10, 10))
    by = {(r.partner_type, r.partner_code): r for r in rows}
    assert by[("channel", "QUNAR")].query_count == 10 and by[("channel", "QUNAR")].quote_count == 6
    assert by[("channel", "QUNAR")].order_count == 2 and by[("channel", "QUNAR")].segment_count == 3
    assert by[("channel", "QUNAR")].blocked_count == 1
    assert by[("supplier", "AH")].query_count == 5
    assert "tm_sso_sid=svc-sid" in seen[0].headers["cookie"]


def test_pagination_stops_when_total_reached():
    pages = []

    def handler(req):
        import json

        page = json.loads(req.content)["PageNo"]
        pages.append(page)
        items = [_item(1, f"C{page}{i}") for i in range(2)]
        return httpx.Response(200, json={"data": {"List": items, "Total": 4}})

    rows = _client(handler).fetch_day(date(2026, 10, 10))
    assert len(rows) == 4 and pages == [1, 2]


def test_duplicate_partner_keeps_last_record():
    def handler(req):
        return httpx.Response(200, json={"data": {"List": [_item(1, "X", LookNum=1), _item(1, "X", LookNum=9)], "Total": 2}})

    rows = _client(handler).fetch_day(date(2026, 10, 10))
    assert len(rows) == 1 and rows[0].query_count == 9


def test_session_expiry_relogs_in_once():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"data": {"List": [], "Total": 0}})

    assert _client(handler).fetch_day(date(2026, 10, 10)) == []
    assert calls["n"] == 2


def test_repeated_auth_failure_raises_auth_error():
    client = _client(lambda req: httpx.Response(401))
    with pytest.raises(ErpAuthError):
        client.fetch_day(date(2026, 10, 10))


def test_business_failure_in_body_raises_upstream_error():
    client = _client(lambda req: httpx.Response(200, json={"Success": False, "UserMsg": "无权限"}))
    with pytest.raises(ErpUpstreamError, match="无权限"):
        client.fetch_day(date(2026, 10, 10))


def test_unknown_partner_type_is_rejected():
    client = _client(lambda req: httpx.Response(200, json={"data": {"List": [_item(9, "X")], "Total": 1}}))
    with pytest.raises(ErpUpstreamError):
        client.fetch_day(date(2026, 10, 10))


def test_network_error_is_upstream_error():
    def handler(req):
        raise httpx.ConnectError("down", request=req)

    with pytest.raises(ErpUpstreamError):
        _client(handler).fetch_day(date(2026, 10, 10))
