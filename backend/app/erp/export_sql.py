"""回填查订比历史数据：用服务账号逐日调用查订比接口，生成可重复执行的 SQL 文件。

用法（在 backend 目录下，--env-file 指向含 ERP 配置的 .env）：
    uv run --env-file ../.env python -m app.erp.export_sql 2026-09-22 2026-10-12 out.sql

- 任意一天拉取失败则整体中止，不写出不完整的文件。
- 生成的语句使用 ON CONFLICT 更新，重复执行结果一致。
- 只覆盖文件中出现的日期与合作方；不会删除数据库中已有但接口未返回的行。
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from app.auth.erp import ErpAuthError, ErpUpstreamError
from app.erp.cache import BEIJING, make_service_client
from app.erp.look2book import Look2BookRow
from app.importers.common import ImportValidationError


def _q(value) -> str:
    """SQL 字面量。字符串转义单引号，数字原样输出。"""
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def render_sql(user_id: int, rows_by_day: dict[date, list[Look2BookRow]], synced_at: datetime) -> str:
    query_values: list[str] = []
    sync_values: list[str] = []
    ts = _q(synced_at.isoformat())
    for day in sorted(rows_by_day):
        rows = rows_by_day[day]
        sync_values.append(f"({user_id}, {_q(day.isoformat())}, {len(rows)}, {ts})")
        for r in rows:
            query_values.append(
                "(" + ", ".join(
                    [
                        str(user_id), _q(day.isoformat()), _q("full_day"), _q("00:00:00"), _q("23:59:59"),
                        _q(r.partner_type), _q(r.partner_code),
                        str(r.query_count), str(r.quote_count), str(r.order_count), str(r.segment_count), str(r.blocked_count),
                        "NULL", _q("erp_api"),
                    ]
                ) + ")"
            )

    lines = ["BEGIN;"]
    if query_values:
        lines += [
            "INSERT INTO query_stat (user_id, biz_date, range_type, period_start, period_end, partner_type, partner_code,",
            "    query_count, quote_count, order_count, segment_count, blocked_count, batch_id, source) VALUES",
            ",\n".join(query_values),
            "ON CONFLICT ON CONSTRAINT uq_query_stat_key DO UPDATE SET",
            "    query_count = EXCLUDED.query_count, quote_count = EXCLUDED.quote_count, order_count = EXCLUDED.order_count,",
            "    segment_count = EXCLUDED.segment_count, blocked_count = EXCLUDED.blocked_count,",
            "    batch_id = EXCLUDED.batch_id, source = EXCLUDED.source;",
        ]
    if sync_values:
        lines += [
            "INSERT INTO look2book_sync (user_id, biz_date, row_count, synced_at) VALUES",
            ",\n".join(sync_values),
            "ON CONFLICT (user_id, biz_date) DO UPDATE SET row_count = EXCLUDED.row_count, synced_at = EXCLUDED.synced_at;",
        ]
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def export(start: date, end: date, out: Path) -> dict:
    client, user_id = make_service_client()
    rows_by_day: dict[date, list[Look2BookRow]] = {}
    try:
        day = start
        while day <= end:
            rows_by_day[day] = client.fetch_day(day)
            print(f"{day}: {len(rows_by_day[day])} 行", file=sys.stderr)
            day += timedelta(days=1)
    finally:
        client.close()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_sql(user_id, rows_by_day, datetime.now(BEIJING)), encoding="utf-8")
    return {"服务账号 UserId": user_id, "天数": len(rows_by_day), "总行数": sum(len(v) for v in rows_by_day.values()), "输出": str(out)}


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 1
    start, end = date.fromisoformat(argv[1]), date.fromisoformat(argv[2])
    if start > end:
        print("开始日期不能晚于结束日期", file=sys.stderr)
        return 1
    try:
        print(export(start, end, Path(argv[3])))
    except (ImportValidationError, ErpAuthError, ErpUpstreamError) as exc:
        print(f"导出失败：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
