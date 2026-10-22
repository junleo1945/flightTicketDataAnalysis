"""数据质量缓存、回填与工具测试，需要 PostgreSQL：设置 TEST_DATABASE_URL 后运行，未设置则跳过。"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

from datetime import date, datetime, time, timedelta  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.auth.erp import ErpUpstreamError  # noqa: E402
from app.erp import quality_backfill, quality_cache as cache  # noqa: E402
from app.erp.data_quality import DataQualityRow  # noqa: E402
from app.importers.common import ImportValidationError  # noqa: E402
from app.metrics.data_quality import quality_diagnose, quality_summary  # noqa: E402
from app.models.analysis import DataQualityStat, DataQualitySync  # noqa: E402
from app.models.base import Base  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="需要 TEST_DATABASE_URL 指向 PostgreSQL")

TABLES = [DataQualityStat.__table__, DataQualitySync.__table__]


def mk(day, agent="AH", supplier="S1", airline="CA", **over):
    base = dict(
        total=10, timeout_5_10=1, timeout_gt_10=0, verify_success=8, verify_fail=2, supplier_success=8, supplier_fail=2,
        supplier_changed=0, unbook=5, booking_success=3, booking_fail=0,
    )
    base.update(over)
    return DataQualityRow(stat_date=day, agent_code=agent, supplier_code=supplier, ticket_carrier=airline,
                          booking_rate=Decimal("0.375"), booking_success_rate=Decimal("0.375"), timeout_rate=Decimal("0.1"), **base)


class FakeSource:
    def __init__(self, rows_by_day=None, fail_days=()):
        self.rows_by_day = rows_by_day or {}
        self.fail_days = set(fail_days)
        self.day_calls: list[date] = []
        self.range_calls: list[tuple[datetime, datetime]] = []
        self.closed = False

    def fetch_day(self, day):
        self.day_calls.append(day)
        if day in self.fail_days:
            raise ErpUpstreamError("boom")
        return self.rows_by_day.get(day, [mk(day)])

    def fetch_range(self, start, end):
        self.range_calls.append((start, end))
        return [mk(start.date(), total=4, verify_success=4, verify_fail=0, supplier_success=4, supplier_fail=0, unbook=4, booking_success=0)]

    def close(self):
        self.closed = True


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
    return cache.beijing_today()


def test_past_day_is_fetched_once_then_read_from_cache(db, today):
    yesterday = today - timedelta(days=1)
    src = FakeSource()
    with db() as s:
        cache.ensure_days(s, [yesterday], src)
        cache.ensure_days(s, [yesterday], src)
    assert src.day_calls == [yesterday]
    with db() as s:
        row = s.scalars(select(DataQualityStat).where(DataQualityStat.stat_date == yesterday)).one()
        assert row.total == 10 and row.agent_code == "AH" and row.booking_rate == Decimal("0.375")
        assert s.get(DataQualitySync, yesterday).row_count == 1


def test_today_is_fetched_live_every_time_and_not_recorded(db, today):
    src = FakeSource()
    with db() as s:
        cache.ensure_days(s, [today], src)
        cache.ensure_days(s, [today], src)
    assert src.day_calls == [today, today]
    with db() as s:
        assert s.scalar(select(func.count()).select_from(DataQualitySync)) == 0
        assert s.scalar(select(func.count()).select_from(DataQualityStat)) == 1


def test_refresh_drops_rows_that_disappeared_and_force_refetches_history(db, today):
    yesterday = today - timedelta(days=1)
    src = FakeSource({yesterday: [mk(yesterday, agent="A"), mk(yesterday, agent="B")]})
    with db() as s:
        cache.ensure_days(s, [yesterday], src)
    src.rows_by_day[yesterday] = [mk(yesterday, agent="A", total=99)]
    with db() as s:
        cache.ensure_days(s, [yesterday], src)  # 已缓存，不重拉
        assert sorted(s.scalars(select(DataQualityStat.agent_code)).all()) == ["A", "B"]
        cache.ensure_days(s, [yesterday], src, force=True)
        rows = s.scalars(select(DataQualityStat)).all()
        assert [(r.agent_code, r.total) for r in rows] == [("A", 99)]


def test_rows_with_other_dates_are_ignored_with_warning(db, today, caplog):
    yesterday = today - timedelta(days=1)
    src = FakeSource({yesterday: [mk(yesterday), mk(yesterday - timedelta(days=5), agent="STRAY")]})
    with db() as s, caplog.at_level("WARNING"):
        cache.ensure_days(s, [yesterday], src)
    with db() as s:
        assert s.scalars(select(DataQualityStat.agent_code)).all() == ["AH"]
        assert s.get(DataQualitySync, yesterday).row_count == 1  # 记实际写入的行数
    assert "其他日期" in caplog.text


def test_fetch_failure_raises_business_error_and_caches_nothing(db, today):
    yesterday = today - timedelta(days=1)
    with db() as s:
        with pytest.raises(ImportValidationError, match="数据质量获取失败"):
            cache.ensure_days(s, [yesterday], FakeSource(fail_days=[yesterday]))
    with db() as s:
        assert s.get(DataQualitySync, yesterday) is None


def test_missing_base_url_is_reported(monkeypatch):
    monkeypatch.setattr(cache, "get_settings", lambda: type("S", (), {"data_quality_base_url": " "})())
    with pytest.raises(ImportValidationError, match="DATA_QUALITY_BASE_URL"):
        cache.make_client()


def test_full_day_load_reads_cache_and_period_load_never_touches_db(db, today):
    d1, d2 = today - timedelta(days=2), today - timedelta(days=1)
    src = FakeSource()
    with db() as s:
        rows = cache.load_rows(s, d1, d2, None, client=src)
        assert sorted(r.stat_date for r in rows) == [d1, d2] and not src.range_calls
        prior = s.scalar(select(func.count()).select_from(DataQualityStat))
        got = cache.load_rows(s, d1, d2, (time(10, 0), time(12, 0)), client=src)
        assert len(got) == 2
        assert s.scalar(select(func.count()).select_from(DataQualityStat)) == prior  # 时段数据不落库
    assert src.range_calls == [
        (datetime.combine(d1, time(10)), datetime.combine(d1, time(12))),
        (datetime.combine(d2, time(10)), datetime.combine(d2, time(12))),
    ]


def test_period_over_limit_and_upstream_failure(db, today):
    with db() as s:
        with pytest.raises(ImportValidationError, match="最多"):
            cache.load_rows(s, today - timedelta(days=9), today, (time(10), time(12)), client=FakeSource())

        class Broken(FakeSource):
            def fetch_range(self, start, end):
                raise ErpUpstreamError("down")

        with pytest.raises(ImportValidationError, match="数据质量获取失败"):
            cache.load_rows(s, today, today, (time(10), time(12)), client=Broken())


# ---- 回填 ----

def test_pending_days_pure():
    today = date(2026, 10, 22)
    synced = {date(2026, 10, 20)}
    assert quality_backfill.pending_days(date(2026, 10, 19), date(2026, 10, 30), synced, today) == [date(2026, 10, 19), date(2026, 10, 21)]
    assert quality_backfill.pending_days(date(2026, 10, 19), date(2026, 10, 30), synced, today, force=True) == [
        date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]
    assert quality_backfill.pending_days(date(2026, 10, 22), date(2026, 10, 30), set(), today) == []  # 起点晚于昨天


def test_backfill_only_fetches_missing_days_and_continues_after_failure(db, today):
    d = [today - timedelta(days=n) for n in (4, 3, 2, 1)]
    src = FakeSource(fail_days=[d[1]])
    with db() as s:
        s.add(DataQualitySync(stat_date=d[0], row_count=0, synced_at=datetime.now()))
        s.commit()
    result = quality_backfill.backfill(d[0], d[3], client=src, session_factory=db)
    assert src.day_calls == [d[1], d[2], d[3]]  # 已同步的 d[0] 跳过
    assert result["成功"] == 2 and list(result["失败"]) == [str(d[1])]
    again = FakeSource()
    quality_backfill.backfill(d[0], d[3], client=again, session_factory=db)
    assert again.day_calls == [d[1]]  # 只补上次失败的那天
    assert not src.closed  # 外部传入的客户端不由回填关闭


# ---- 工具 ----

def test_summary_and_diagnose_full_day_use_cached_data(db, today):
    d = today - timedelta(days=1)
    src = FakeSource({d: [mk(d, agent="AH", supplier="S1", airline="CA"), mk(d, agent="AH", supplier="S2", airline="MU", total=200, verify_success=100, verify_fail=100, supplier_success=100, supplier_fail=100)]})
    with db() as s:
        out = quality_summary(s, str(d), str(d), "supplier", sort_by="supplier_fail_rate", client=src)
        names = [g["供应商"] for g in out["分组明细（最多 50 条）"]]
        assert names == ["S2", "S1"] and out["合计"]["验价总数"] == 210 and out["范围"] == "完整一天"
        diag = quality_diagnose(s, str(d), str(d), "AH", client=src)
        assert diag["主要异常供应商"][0] == "S2" and diag["渠道层"]["渠道侧判定"] == "重点关注"
        empty = quality_summary(s, str(d), str(d), "channel", agent_code="NOPE", client=src)
        assert "没有符合条件" in empty["结论"]


def test_summary_period_flags_today_note(db, today):
    with db() as s:
        out = quality_summary(s, str(today), str(today), "overall", range_type="period", period_start="10:00", period_end="12:00", client=FakeSource())
        assert out["范围"] == "每天 10:00～12:00" and "实时" in out["注意"]
