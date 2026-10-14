"""查订比缓存测试，需要 PostgreSQL：设置 TEST_DATABASE_URL 后运行，未设置则跳过。"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

from datetime import date, datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.erp import cache as cache_mod  # noqa: E402
from app.erp.look2book import Look2BookRow  # noqa: E402
from app.importers.common import ImportValidationError  # noqa: E402
from app.metrics.query_ratio import l2b_anomaly  # noqa: E402
from app.models.analysis import Look2BookSync, QueryStat  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.upload import UploadBatch  # noqa: E402
from app.auth.erp import ErpUpstreamError  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="需要 TEST_DATABASE_URL 指向 PostgreSQL")

TABLES = [UploadBatch.__table__, QueryStat.__table__, Look2BookSync.__table__]
USER, SID = 1, "user-sid"


class FakeSource:
    def __init__(self, rows_by_day=None, fail=False):
        self.rows_by_day = rows_by_day or {}
        self.fail = fail
        self.calls: list[date] = []

    def fetch_day(self, day):
        self.calls.append(day)
        if self.fail:
            raise ErpUpstreamError("boom")
        return self.rows_by_day.get(day, [Look2BookRow("channel", "QUNAR", 10, 5, 2, 3, 1)])

    def close(self):
        pass


@pytest.fixture()
def db():
    engine = create_engine(TEST_DB)
    Base.metadata.drop_all(engine, tables=TABLES)
    Base.metadata.create_all(engine, tables=TABLES)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.drop_all(engine, tables=TABLES)
    engine.dispose()


@pytest.fixture()
def today():
    return cache_mod.beijing_today()


def test_past_day_is_fetched_once_then_read_from_cache(db, today):
    yesterday = today - timedelta(days=1)
    src = FakeSource()
    with db() as s:
        cache_mod.ensure_days(s, [yesterday], USER, SID, client=src)
        cache_mod.ensure_days(s, [yesterday], USER, SID, client=src)
    assert src.calls == [yesterday]
    with db() as s:
        row = s.scalars(select(QueryStat).where(QueryStat.biz_date == yesterday)).one()
        assert row.source == "erp_api" and row.batch_id is None and row.query_count == 10
        assert s.get(Look2BookSync, (USER, yesterday)).row_count == 1


def test_today_is_fetched_live_every_time_and_not_recorded(db, today):
    src = FakeSource()
    with db() as s:
        cache_mod.ensure_days(s, [today], USER, SID, client=src)
        cache_mod.ensure_days(s, [today], USER, SID, client=src)
    assert src.calls == [today, today]
    with db() as s:
        assert s.scalar(select(func.count()).select_from(Look2BookSync)) == 0
        assert s.scalar(select(func.count()).select_from(QueryStat).where(QueryStat.biz_date == today)) == 1


def test_refreshing_today_drops_rows_that_disappeared(db, today):
    src = FakeSource({today: [Look2BookRow("channel", "A", 1, 1, 1, 1, 0), Look2BookRow("channel", "B", 2, 2, 2, 2, 0)]})
    with db() as s:
        cache_mod.ensure_days(s, [today], USER, SID, client=src)
    src.rows_by_day[today] = [Look2BookRow("channel", "A", 5, 1, 1, 1, 0)]
    with db() as s:
        cache_mod.ensure_days(s, [today], USER, SID, client=src)
        codes = s.scalars(select(QueryStat.partner_code).where(QueryStat.biz_date == today)).all()
        assert codes == ["A"]


def test_fetch_failure_raises_business_error_and_caches_nothing(db, today):
    yesterday = today - timedelta(days=1)
    with db() as s:
        with pytest.raises(ImportValidationError, match="查订比数据获取失败"):
            cache_mod.ensure_days(s, [yesterday], USER, SID, client=FakeSource(fail=True))
    with db() as s:
        assert s.get(Look2BookSync, (USER, yesterday)) is None


def test_missing_erp_session_is_reported():
    with pytest.raises(ImportValidationError, match="ERP 登录"):
        cache_mod.make_client("")


def test_l2b_anomaly_fetches_history_window_from_erp(db, today, monkeypatch):
    yesterday = today - timedelta(days=1)
    src = FakeSource()
    monkeypatch.setattr(cache_mod, "make_client", lambda sid: src)
    with db() as s:
        result = l2b_anomaly(s, yesterday.isoformat(), 2, "overall", user_id=USER, erp_sid=SID)
    # 历史 2 天 + 查询日本身 = 3 天，全部从接口获取
    assert sorted(src.calls) == sorted([yesterday - timedelta(days=2), yesterday - timedelta(days=1), yesterday])
    assert "注意" not in result and result["日期"] == str(yesterday)


def test_l2b_anomaly_marks_today_as_partial(db, today, monkeypatch):
    monkeypatch.setattr(cache_mod, "make_client", lambda sid: FakeSource())
    with db() as s:
        result = l2b_anomaly(s, today.isoformat(), 1, "overall", user_id=USER, erp_sid=SID)
    assert "注意" in result and "累计" in result["注意"]
