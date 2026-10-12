"""查订比数据的按需缓存。

- 历史整天：第一次需要时从 ERP 拉取，写入 query_stat 并记入 look2book_sync，之后直接读库。
- 当天：每次需要时实时拉取，覆盖写入 query_stat，但不记入同步表（数据是截至拉取时刻的累计）。
"""

from datetime import date, datetime, time, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.erp import ErpAuthError, ErpUpstreamError
from app.core.config import get_settings
from app.core.upsert import upsert
from app.erp.look2book import Look2BookClient, Look2BookRow
from app.importers.common import ImportValidationError
from app.models.analysis import Look2BookSync, QueryStat

BEIJING = ZoneInfo("Asia/Shanghai")
FULL_DAY_START, FULL_DAY_END = time(0, 0, 0), time(23, 59, 59)
KEY_COLS = ["biz_date", "range_type", "period_start", "period_end", "partner_type", "partner_code"]


class Look2BookSource(Protocol):
    def fetch_day(self, day: date) -> list[Look2BookRow]: ...
    def close(self) -> None: ...


def beijing_today() -> date:
    return datetime.now(BEIJING).date()


def make_client() -> Look2BookClient:
    settings = get_settings()
    if not settings.erp_service_username or not settings.erp_service_password:
        raise ImportValidationError("未配置 ERP 服务账号（.env 中的 ERP_SERVICE_USERNAME / ERP_SERVICE_PASSWORD），无法获取查订比数据")
    return Look2BookClient(settings.erp_base_url, settings.erp_service_username, settings.erp_service_password)


def _replace_day(session: Session, day: date, rows: list[Look2BookRow]) -> None:
    """替换某天从接口来的数据。若 Excel 上传的同日期数据键相同，会被接口数据覆盖。"""
    session.execute(
        delete(QueryStat).where(
            QueryStat.biz_date == day,
            QueryStat.range_type == "full_day",
            QueryStat.source == "erp_api",
        )
    )
    upsert(
        session.connection(),
        QueryStat.__table__,
        [
            {
                "biz_date": day,
                "range_type": "full_day",
                "period_start": FULL_DAY_START,
                "period_end": FULL_DAY_END,
                "partner_type": r.partner_type,
                "partner_code": r.partner_code,
                "query_count": r.query_count,
                "quote_count": r.quote_count,
                "order_count": r.order_count,
                "segment_count": r.segment_count,
                "blocked_count": r.blocked_count,
                "batch_id": None,
                "source": "erp_api",
            }
            for r in rows
        ],
        KEY_COLS,
    )


def ensure_days(session: Session, days: list[date], client: Look2BookSource | None = None) -> None:
    """保证 days 中每一天的查订比数据可用：历史缺的补拉，当天实时拉取。"""
    if not days:
        return
    today = beijing_today()
    past = sorted({d for d in days if d < today})
    need_today = today in days
    if not past and not need_today:
        return

    own_client = client is None
    source: Look2BookSource = client or make_client()
    try:
        cached: set[date] = set()
        if past:
            cached = set(session.scalars(select(Look2BookSync.biz_date).where(Look2BookSync.biz_date.in_(past))).all())
        for d in past:
            if d in cached:
                continue
            rows = source.fetch_day(d)
            _replace_day(session, d, rows)
            session.merge(Look2BookSync(biz_date=d, row_count=len(rows), synced_at=datetime.now(timezone.utc)))
            session.commit()
        if need_today:
            _replace_day(session, today, source.fetch_day(today))
            session.commit()
    except (ErpAuthError, ErpUpstreamError) as exc:
        session.rollback()
        raise ImportValidationError(f"查订比数据获取失败：{exc}") from exc
    finally:
        if own_client:
            source.close()
