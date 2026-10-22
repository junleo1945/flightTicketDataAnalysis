import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

import logging  # noqa: E402
from datetime import date, timedelta  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from app.erp import sync  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")


class Stop(Exception):
    pass


def _settings(**kw):
    return type("S", (), {"data_quality_base_url": "http://dq", "data_quality_backfill_from": "2026-09-22", **kw})()


def test_one_failing_daily_task_does_not_stop_the_other(monkeypatch):
    calls = []
    sleeps = []

    def fake_sleep(_):
        sleeps.append(1)
        if len(sleeps) > 1:
            raise Stop

    def boom(day):
        calls.append(("查订比", day))
        raise RuntimeError("erp down")

    monkeypatch.setattr(sync, "run_startup_backfill", lambda: calls.append("backfill"))
    monkeypatch.setattr(sync, "sleep", fake_sleep)
    monkeypatch.setattr(sync, "sync_day", boom)
    monkeypatch.setattr(sync, "sync_quality_day", lambda day: calls.append(("数据质量", day)))
    with pytest.raises(Stop):
        sync._run_forever()
    assert calls[0] == "backfill"
    assert [c[0] for c in calls[1:]] == ["查订比", "数据质量"]
    assert calls[1][1] == calls[2][1] == sync.beijing_today() - timedelta(days=1)


def test_startup_backfill_skips_without_config(monkeypatch, caplog):
    called = []
    monkeypatch.setattr(sync, "backfill", lambda *a, **k: called.append(1))
    monkeypatch.setattr(sync, "get_settings", lambda: _settings(data_quality_backfill_from=" "))
    sync.run_startup_backfill()
    monkeypatch.setattr(sync, "get_settings", lambda: _settings(data_quality_base_url=""))
    with caplog.at_level(logging.WARNING):
        sync.run_startup_backfill()
    assert "DATA_QUALITY_BASE_URL" in caplog.text
    monkeypatch.setattr(sync, "get_settings", lambda: _settings(data_quality_backfill_from="not-a-date"))
    sync.run_startup_backfill()
    monkeypatch.setattr(sync, "get_settings", lambda: _settings(data_quality_backfill_from=str(date.today() + timedelta(days=5))))
    sync.run_startup_backfill()
    assert called == []


@pytest.mark.skipif(not TEST_DB, reason="需要 TEST_DATABASE_URL 指向 PostgreSQL")
def test_advisory_lock_is_exclusive_and_released_and_backfill_runs_under_it(monkeypatch):
    monkeypatch.setattr(sync, "engine", create_engine(TEST_DB))
    with sync.advisory_lock(990001) as first:
        assert first is True
        with sync.advisory_lock(990001) as second:
            assert second is False  # 另一个实例（另一个连接）拿不到
    with sync.advisory_lock(990001) as again:
        assert again is True  # 释放后可再次获取

    ran = []
    monkeypatch.setattr(sync, "QUALITY_LOCK_KEY", 990002)
    monkeypatch.setattr(sync, "get_settings", lambda: _settings())
    monkeypatch.setattr(sync, "backfill", lambda start, end: ran.append((start, end)) or {"成功": 0})
    with sync.advisory_lock(990002):
        sync.run_startup_backfill()  # 别处正在回填：跳过
    assert ran == []
    sync.run_startup_backfill()
    assert ran == [(date(2026, 9, 22), sync.beijing_today() - timedelta(days=1))]


def test_backfill_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(sync, "get_settings", lambda: _settings())

    class L:
        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(sync, "advisory_lock", lambda key: L())
    sync.run_startup_backfill()  # 不抛异常
