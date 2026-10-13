import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/test")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://example.invalid/v1")
os.environ.setdefault("AGENT_MODEL", "test-agent")
os.environ.setdefault("WORKFLOW_MODEL", "test-workflow")

from datetime import date, datetime  # noqa: E402

from app.erp.cache import BEIJING  # noqa: E402
from app.erp.export_sql import render_sql  # noqa: E402
from app.erp.look2book import Look2BookRow  # noqa: E402
from app.erp.sync import seconds_until_next_midnight  # noqa: E402


def test_seconds_until_next_midnight_is_beijing_midnight():
    now = datetime(2026, 10, 12, 23, 30, tzinfo=BEIJING)
    assert seconds_until_next_midnight(now) == 30 * 60


def test_render_sql_escapes_codes_and_upserts_both_tables():
    rows = {date(2026, 10, 1): [Look2BookRow("channel", "O'BRIEN", 10, 5, 2, 3, 1)], date(2026, 10, 2): []}
    sql = render_sql(42, rows, datetime(2026, 10, 13, 0, 5, tzinfo=BEIJING))
    assert "'O''BRIEN'" in sql
    assert "(42, '2026-10-01', 'full_day', '00:00:00', '23:59:59', 'channel', 'O''BRIEN', 10, 5, 2, 3, 1, NULL, 'erp_api')" in sql
    assert "(42, '2026-10-02', 0, '2026-10-13T00:05:00+08:00')" in sql
    assert "ON CONFLICT ON CONSTRAINT uq_query_stat_key" in sql
    assert "ON CONFLICT (user_id, biz_date)" in sql
    assert sql.startswith("BEGIN;") and sql.rstrip().endswith("COMMIT;")


def test_render_sql_without_rows_only_marks_sync():
    sql = render_sql(7, {date(2026, 10, 1): []}, datetime(2026, 10, 13, tzinfo=BEIJING))
    assert "query_stat" not in sql
    assert "look2book_sync" in sql
