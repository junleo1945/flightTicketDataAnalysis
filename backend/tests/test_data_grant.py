"""数据授权测试，需要 PostgreSQL：设置 TEST_DATABASE_URL 后运行，未设置则跳过。"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

from datetime import date, datetime, timedelta, timezone  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, delete  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api import deps as deps_api  # noqa: E402
from app.api import grants as grants_api  # noqa: E402
from app.api import uploads as uploads_api  # noqa: E402
from app.auth.erp import ErpAuthError, ErpUser  # noqa: E402
from app.auth.limiter import FixedWindowLimiter  # noqa: E402
from app.auth.service import AuthService  # noqa: E402
from app.main import app  # noqa: E402
from app.metrics.common import STATUS_DONE  # noqa: E402
from app.metrics.profit import load_orders  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.grant import DataGrant  # noqa: E402
from app.models.order import OrderAirline, OrderRoute, SupplierOrder, TmOrder  # noqa: E402
from app.models.auth import AuthSession, User  # noqa: E402
from app.models.analysis import QueryStat  # noqa: E402
from app.models.upload import RawOrderRow, UploadBatch  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="需要 TEST_DATABASE_URL 指向 PostgreSQL")

ZS, LS, WW = 990101, 990102, 990103  # 张三 / 李四 / 王五


class FakeErp:
    def __init__(self):
        self.users = {
            "zhangsan": ErpUser(ZS, "zhangsan", "张三", 3600, "erp-zs"),
            "lisi": ErpUser(LS, "lisi", "李四", 3600, "erp-ls"),
            "wangwu": ErpUser(WW, "wangwu", "王五", 3600, "erp-ww"),
        }

    def authenticate(self, username, password):
        if password != "secret" or username not in self.users:
            raise ErpAuthError("用户名或密码错误")
        return self.users[username]

    def verify(self, erp_sid):
        return next(u for u in self.users.values() if u.erp_sid == erp_sid)


@pytest.fixture()
def db():
    engine = create_engine(TEST_DB)
    tables = [User.__table__, AuthSession.__table__, DataGrant.__table__, UploadBatch.__table__, TmOrder.__table__,
              SupplierOrder.__table__, OrderAirline.__table__, OrderRoute.__table__, RawOrderRow.__table__,
              QueryStat.__table__]
    Base.metadata.drop_all(engine, tables=tables)
    Base.metadata.create_all(engine, tables=tables)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.drop_all(engine, tables=tables)
    engine.dispose()


@pytest.fixture()
def client(db, monkeypatch):
    auth = AuthService(db, FakeErp(), verify_interval_seconds=300, now=lambda: datetime.now(timezone.utc))
    app.dependency_overrides[deps_api.get_auth_service] = lambda: auth
    app.dependency_overrides[deps_api.get_login_limiter] = lambda: FixedWindowLimiter(limit=100, window_seconds=60)
    monkeypatch.setattr(grants_api, "SessionLocal", db)
    monkeypatch.setattr(uploads_api, "SessionLocal", db)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, username):
    return client.post("/api/user/login", json={"username": username, "password": "secret"})


def _seed_users(db):
    with db() as s, s.begin():
        s.add_all([User(id=ZS, username="zhangsan", display_name="张三"),
                   User(id=LS, username="lisi", display_name="李四"),
                   User(id=WW, username="wangwu", display_name="王五")])


# ---------- 分析与问答不受授权影响 ----------

def test_analysis_does_not_read_owner_orders_even_when_granted(db):
    _seed_users(db)
    with db() as s, s.begin():
        s.add(UploadBatch(id=1, file_type="order", file_name="a.xlsx", file_hash="h", user_id=ZS))
    with db() as s, s.begin():  # 批次先于订单落库（batch_id 外键）
        s.add(TmOrder(tm_order_no="T1", user_id=ZS, batch_id=1, channel_code="C1", order_status=STATUS_DONE,
                      pay_date=date(2026, 10, 10), total_price=Decimal("100"), passenger_count=1))
        s.add(DataGrant(owner_user_id=ZS, grantee_user_id=LS))
    day = date(2026, 10, 10)
    with db() as s:
        assert load_orders(s, day, day, {}, ZS)  # 拥有者本人可见
        assert load_orders(s, day, day, {}, LS) == []  # 被授权人不能用分析读到拥有者数据


# ---------- 批次明细可见性 ----------

def _seed_batches(db):
    _seed_users(db)
    with db() as s, s.begin():
        s.add(UploadBatch(id=1, file_type="order", file_name="a.xlsx", file_hash="h1", user_id=ZS))
        s.add(UploadBatch(id=2, file_type="order", file_name="b.xlsx", file_hash="h2", user_id=WW))
    with db() as s, s.begin():
        s.add(RawOrderRow(batch_id=1, sheet_name="采购维度", row_no=2, data={"途灵订单号": "T1", "采购客户": "C1"}))
        s.add(RawOrderRow(batch_id=2, sheet_name="采购维度", row_no=2, data={"途灵订单号": "T9"}))


def test_owner_sees_own_batch_rows(client, db):
    _seed_batches(db)
    _login(client, "zhangsan")
    body = client.get("/api/uploads/1/rows").json()
    assert body["total"] == 1 and body["rows"][0]["途灵订单号"] == "T1"
    assert body["columns"][:2] == ["sheet_name", "row_no"]


def test_grantee_sees_granted_batch_rows_and_loses_after_revoke(client, db):
    _seed_batches(db)
    with db() as s, s.begin():
        s.add(DataGrant(owner_user_id=ZS, grantee_user_id=LS))
    _login(client, "lisi")
    assert client.get("/api/uploads/1/rows").status_code == 200
    assert client.get("/api/uploads/2/rows").status_code == 404  # 王五的批次未授权
    with db() as s, s.begin():
        s.execute(delete(DataGrant))
    assert client.get("/api/uploads/1/rows").status_code == 404  # 撤销后立即不可见


def test_stranger_cannot_see_rows(client, db):
    _seed_batches(db)
    _login(client, "lisi")
    assert client.get("/api/uploads/1/rows").status_code == 404


# ---------- 授权接口 ----------

def test_grant_lifecycle(client, db):
    _seed_users(db)
    assert _login(client, "zhangsan").status_code == 200
    r = client.post("/api/data-grants", json={"username": "lisi"})
    assert r.status_code == 201 and r.json()["user_id"] == LS
    assert client.post("/api/data-grants", json={"username": "lisi"}).status_code == 201  # 重复授权不报错
    assert client.get("/api/data-grants").json() == {
        "granted_to_me": [], "granted_by_me": [{"user_id": LS, "username": "lisi", "display_name": "李四"}],
    }
    assert client.delete(f"/api/data-grants/{LS}").status_code == 204
    assert client.get("/api/data-grants").json()["granted_by_me"] == []


def test_grant_rejects_self_and_unknown_user(client, db):
    _seed_users(db)
    _login(client, "zhangsan")
    assert client.post("/api/data-grants", json={"username": "zhangsan"}).status_code == 400
    assert client.post("/api/data-grants", json={"username": "nobody"}).status_code == 404


def test_listing_shows_grants_received(client, db):
    _seed_users(db)
    with db() as s, s.begin():
        s.add(DataGrant(owner_user_id=ZS, grantee_user_id=LS))
    _login(client, "lisi")
    body = client.get("/api/data-grants").json()
    assert [u["username"] for u in body["granted_to_me"]] == ["zhangsan"]
    assert body["granted_by_me"] == []
