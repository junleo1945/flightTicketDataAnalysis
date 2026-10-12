"""会话与接口测试，需要 PostgreSQL：设置 TEST_DATABASE_URL 后运行，未设置则跳过。"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, delete, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api import chat as chat_api  # noqa: E402
from app.api import deps as deps_api  # noqa: E402
from app.api import uploads as uploads_api  # noqa: E402
from app.auth.erp import ErpAuthError, ErpUpstreamError, ErpUser  # noqa: E402
from app.auth.limiter import FixedWindowLimiter  # noqa: E402
from app.auth.service import AuthService  # noqa: E402
from app.main import app  # noqa: E402
from app.models.auth import AuthSession, User  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.chat import ChatLog, Conversation  # noqa: E402
from app.models.upload import UploadBatch  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="需要 TEST_DATABASE_URL 指向 PostgreSQL")


class FakeErp:
    """替代真实 ERP：记录复核次数，并可切换为失效或故障。"""

    def __init__(self):
        self.users = {"zhangsan": ErpUser(990001, "zhangsan", "张三", 3600, "erp-zs")}
        self.verify_calls = 0
        self.mode = "ok"  # ok / expired / down

    def authenticate(self, username, password):
        if password != "secret" or username not in self.users:
            raise ErpAuthError("用户名或密码错误")
        return self.users[username]

    def verify(self, erp_sid):
        self.verify_calls += 1
        if self.mode == "down":
            raise ErpUpstreamError("down")
        if self.mode == "expired":
            raise ErpAuthError("未登录")
        return self.users["zhangsan"]


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture()
def db():
    engine = create_engine(TEST_DB)
    tables = [User.__table__, AuthSession.__table__, Conversation.__table__, ChatLog.__table__, UploadBatch.__table__]
    Base.metadata.drop_all(engine, tables=tables)
    Base.metadata.create_all(engine, tables=tables)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.drop_all(engine, tables=tables)
    engine.dispose()


@pytest.fixture()
def erp():
    return FakeErp()


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def auth(db, erp, clock):
    return AuthService(db, erp, verify_interval_seconds=300, now=clock)


@pytest.fixture()
def limiter():
    return FixedWindowLimiter(limit=100, window_seconds=60)


@pytest.fixture()
def client(db, auth, limiter, monkeypatch):
    app.dependency_overrides[deps_api.get_auth_service] = lambda: auth
    app.dependency_overrides[deps_api.get_login_limiter] = lambda: limiter
    monkeypatch.setattr(chat_api, "SessionLocal", db)
    monkeypatch.setattr(uploads_api, "SessionLocal", db)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, username="zhangsan", password="secret"):
    return client.post("/api/user/login", json={"username": username, "password": password})


# ---------- AuthService ----------

def test_login_creates_user_and_session(auth, db):
    result = auth.login("zhangsan", "secret")
    assert result.user.id == 990001
    with db() as s:
        assert s.get(User, 990001).username == "zhangsan"
        row = s.get(AuthSession, result.sid)
        assert row.erp_sid == "erp-zs" and row.user_id == 990001


def test_verify_skips_erp_within_interval(auth, erp, clock):
    sid = auth.login("zhangsan", "secret").sid
    clock.now += timedelta(seconds=299)
    assert auth.verify(sid).id == 990001
    assert erp.verify_calls == 0


def test_verify_after_interval_rechecks_and_refreshes(auth, erp, clock, db):
    sid = auth.login("zhangsan", "secret").sid
    clock.now += timedelta(seconds=301)
    assert auth.verify(sid) is not None
    assert erp.verify_calls == 1
    with db() as s:
        assert s.get(AuthSession, sid).verified_at == clock.now


def test_erp_rejects_session_destroys_it(auth, erp, clock, db):
    sid = auth.login("zhangsan", "secret").sid
    erp.mode = "expired"
    clock.now += timedelta(seconds=301)
    assert auth.verify(sid) is None
    with db() as s:
        assert s.get(AuthSession, sid) is None


def test_erp_outage_keeps_session(auth, erp, clock, db):
    sid = auth.login("zhangsan", "secret").sid
    erp.mode = "down"
    clock.now += timedelta(seconds=301)
    with pytest.raises(ErpUpstreamError):
        auth.verify(sid)
    with db() as s:
        assert s.get(AuthSession, sid) is not None


def test_expired_local_session_is_rejected(auth, clock):
    sid = auth.login("zhangsan", "secret").sid
    clock.now += timedelta(hours=25)
    assert auth.verify(sid) is None


def test_session_lifetime_capped_by_erp_expiry(auth, erp, clock, db):
    erp.users["zhangsan"] = ErpUser(990001, "zhangsan", "张三", 600, "erp-zs")
    result = auth.login("zhangsan", "secret")
    assert result.max_age_seconds == 600
    with db() as s:
        assert s.get(AuthSession, result.sid).expires_at == clock.now + timedelta(seconds=600)


# ---------- HTTP ----------

def test_protected_routes_require_login(client):
    assert client.get("/api/uploads").status_code == 401
    r = client.post("/api/chat/stream", json={"question": "hi"})
    assert r.status_code == 401 and r.json()["code"] == 40001
    assert client.get("/api/chat/conversations").status_code == 401


def test_health_stays_public(client):
    assert client.get("/api/health").status_code == 200


def test_login_wrong_password_is_401(client):
    assert _login(client, password="bad").status_code == 401


def test_login_sets_httponly_cookie_and_info_works(client):
    r = _login(client)
    assert r.status_code == 200
    assert r.json() == {"username": "zhangsan", "display_name": "张三"}
    cookie = r.headers["set-cookie"]
    assert "fta_sid=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    info = client.get("/api/user/info")
    assert info.status_code == 200 and info.json()["id"] == 990001


def test_logout_invalidates_session(client):
    _login(client)
    assert client.post("/api/user/logout").status_code == 200
    assert client.get("/api/user/info").status_code == 401


def test_login_rate_limited(client):
    tight = FixedWindowLimiter(limit=1, window_seconds=60)
    app.dependency_overrides[deps_api.get_login_limiter] = lambda: tight
    assert _login(client, password="bad").status_code == 401
    assert _login(client, password="bad").status_code == 429


def _make_conversation(db, user_id, title, *, pinned=False, questions=()):
    with db() as s, s.begin():
        conv = Conversation(user_id=user_id, title=title, pinned=pinned)
        s.add(conv)
        s.flush()
        for q in questions:
            s.add(ChatLog(conversation_id=conv.id, question=q, answer="a", model="m", latency_ms=1, trace=[], user_id=user_id))
        return conv.id


def _add_lisi(db):
    with db() as s, s.begin():
        s.add(User(id=990002, username="lisi", display_name="李四"))


def test_conversations_are_private_and_pinned_first(client, db):
    _login(client)
    _add_lisi(db)
    _make_conversation(db, 990001, "普通会话", questions=["q1"])
    _make_conversation(db, 990001, "收藏会话", pinned=True, questions=["q2"])
    _make_conversation(db, 990002, "别人的会话", questions=["q3"])
    titles = [c["title"] for c in client.get("/api/chat/conversations").json()]
    assert titles == ["收藏会话", "普通会话"]


def test_get_conversation_returns_messages_in_order(client, db):
    _login(client)
    cid = _make_conversation(db, 990001, "会话", questions=["第一问", "第二问"])
    body = client.get(f"/api/chat/conversations/{cid}").json()
    assert [m["question"] for m in body["messages"]] == ["第一问", "第二问"]
    assert body["title"] == "会话"


def test_other_users_conversation_is_not_found(client, db):
    _login(client)
    _add_lisi(db)
    cid = _make_conversation(db, 990002, "别人的")
    assert client.get(f"/api/chat/conversations/{cid}").status_code == 404
    assert client.patch(f"/api/chat/conversations/{cid}", json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/chat/conversations/{cid}").status_code == 404


def test_rename_and_pin(client, db):
    _login(client)
    cid = _make_conversation(db, 990001, "旧标题")
    r = client.patch(f"/api/chat/conversations/{cid}", json={"title": "  新标题  ", "pinned": True})
    assert r.status_code == 200 and r.json()["title"] == "新标题" and r.json()["pinned"] is True
    assert client.patch(f"/api/chat/conversations/{cid}", json={"title": "   "}).status_code == 400


def test_delete_removes_conversation_and_its_messages(client, db):
    _login(client)
    cid = _make_conversation(db, 990001, "待删除", questions=["q1", "q2"])
    assert client.delete(f"/api/chat/conversations/{cid}").json() == {"ok": True}
    assert client.get(f"/api/chat/conversations/{cid}").status_code == 404
    with db() as s:
        assert s.scalars(select(ChatLog).where(ChatLog.conversation_id == cid)).all() == []


def test_stream_rejects_conversation_of_another_user(client, db):
    _login(client)
    _add_lisi(db)
    cid = _make_conversation(db, 990002, "别人的")
    r = client.post("/api/chat/stream", json={"question": "hi", "conversation_id": cid})
    assert r.status_code == 404


def test_uploads_list_is_shared_across_users(client, db):
    _login(client)
    with db() as s, s.begin():
        s.add(User(id=990002, username="lisi", display_name="李四"))
        s.add(UploadBatch(file_type="order", file_name="a.xlsx", file_hash="h1", user_id=990002))
    rows = client.get("/api/uploads").json()
    assert len(rows) == 1 and rows[0]["file_name"] == "a.xlsx"
