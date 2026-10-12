import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.auth.erp import ErpAuthError, ErpClient, ErpUpstreamError  # noqa: E402
from app.auth.limiter import FixedWindowLimiter  # noqa: E402

BASE = "https://erp.test"


def _client(handler) -> ErpClient:
    return ErpClient(BASE, transport=httpx.MockTransport(handler))


def _user_body(**data) -> dict:
    base = {"UserId": 990001, "UserName": "zhangsan", "FullName": "张三", "ExpireDuration": 3600}
    base.update(data)
    return {"Success": True, "Data": base}


def test_authenticate_returns_user_and_forwards_password_only():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/login":
            seen["body"] = req.content.decode()
            return httpx.Response(200, json={"Success": True}, headers={"set-cookie": "tm_sso_sid=sid-1; Path=/; HttpOnly"})
        assert req.url.path == "/user/api/get"
        assert "tm_sso_sid=sid-1" in req.headers["cookie"]
        return httpx.Response(200, json=_user_body())

    user = _client(handler).authenticate("zhangsan", "pw")
    assert user.user_id == 990001 and user.username == "zhangsan" and user.display_name == "张三"
    assert user.erp_sid == "sid-1" and user.expire_seconds == 3600
    assert "pw" in seen["body"]  # 密码透传给 ERP


def test_wrong_password_raises_auth_error():
    client = _client(lambda req: httpx.Response(401, json={"Success": False}))
    with pytest.raises(ErpAuthError):
        client.authenticate("zhangsan", "bad")


def test_business_error_in_200_body_raises_auth_error():
    client = _client(lambda req: httpx.Response(200, json={"Success": False, "UserMsg": "密码错误"}))
    with pytest.raises(ErpAuthError, match="密码错误"):
        client.authenticate("zhangsan", "bad")


def test_login_without_sso_cookie_is_upstream_error():
    client = _client(lambda req: httpx.Response(200, json={"Success": True}))
    with pytest.raises(ErpUpstreamError):
        client.authenticate("zhangsan", "pw")


def test_server_error_is_upstream_error():
    client = _client(lambda req: httpx.Response(500))
    with pytest.raises(ErpUpstreamError):
        client.authenticate("zhangsan", "pw")


def test_network_error_is_upstream_error():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=req)

    with pytest.raises(ErpUpstreamError):
        _client(handler).verify("sid")


def test_verify_redirect_means_logged_out():
    client = _client(lambda req: httpx.Response(302, headers={"location": "/login"}))
    with pytest.raises(ErpAuthError):
        client.verify("sid")


@pytest.mark.parametrize(
    "raw, expected",
    [
        (3600, 3600),  # 秒
        (3_600_000_000_000, 3600),  # 纳秒
        (0, 86400),  # 无效值回退到 24 小时
    ],
)
def test_expire_duration_normalised(raw, expected):
    client = _client(lambda req: httpx.Response(200, json=_user_body(ExpireDuration=raw)))
    assert client.verify("sid").expire_seconds == expected


def test_limiter_blocks_after_limit_and_resets_after_window():
    t = [0.0]
    limiter = FixedWindowLimiter(limit=2, window_seconds=60, now=lambda: t[0])
    assert limiter.hit("ip:1") and limiter.hit("ip:1")
    assert not limiter.hit("ip:1")
    assert limiter.hit("ip:2")  # 不同 key 互不影响
    t[0] = 61
    assert limiter.hit("ip:1")
