"""数据质量数据的按需缓存与读取。

- 全局一份：接口免鉴权，对所有人返回同样的数据，缓存不按用户隔离。
- 历史整天：第一次需要时从接口拉取，写入 data_quality_stat 并记入 data_quality_sync，之后直接读库。
- 当天：每次需要时实时拉取，覆盖写入 data_quality_stat，但不记入同步表（数据是截至拉取时刻的累计）。
- 时间段（小时级）：每次实时调接口，只在内存里使用，不落库。
"""

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.erp import ErpAuthError, ErpUpstreamError
from app.core.config import get_settings
from app.core.upsert import upsert
from app.erp.cache import beijing_today
from app.erp.data_quality import COUNT_FIELDS, RATE_FIELDS, DataQualityClient, DataQualityRow
from app.importers.common import ImportValidationError
from app.models.analysis import DataQualityStat, DataQualitySync

log = logging.getLogger(__name__)

KEY_COLS = ["stat_date", "agent_code", "supplier_code", "ticket_carrier"]
MAX_PERIOD_DAYS = 7  # 时间段每天要调一次接口，限制跨度避免一次问答等太久


class QualitySource(Protocol):
    def fetch_range(self, start: datetime, end: datetime) -> list[DataQualityRow]: ...
    def fetch_day(self, day: date) -> list[DataQualityRow]: ...
    def close(self) -> None: ...


def make_client() -> DataQualityClient:
    url = get_settings().data_quality_base_url.strip()
    if not url:
        raise ImportValidationError("未配置 DATA_QUALITY_BASE_URL，无法获取数据质量数据")
    return DataQualityClient(url)


def period_bounds(day: date, start: time, end: time) -> tuple[datetime, datetime]:
    """某天的时间段对应的接口区间：[开始, 结束]。结束为 23:59 时取到当天最后一秒，与整天口径一致。"""
    end_dt = datetime.combine(day, end)
    if end == time(23, 59):
        end_dt = end_dt.replace(second=59)
    return datetime.combine(day, start), end_dt


def _to_record(r: DataQualityRow) -> dict:
    return {
        "stat_date": r.stat_date, "agent_code": r.agent_code, "supplier_code": r.supplier_code, "ticket_carrier": r.ticket_carrier,
        **{f: getattr(r, f) for f in COUNT_FIELDS}, **{f: getattr(r, f) for f in RATE_FIELDS},
    }


def _from_record(m: DataQualityStat) -> DataQualityRow:
    return DataQualityRow(
        stat_date=m.stat_date, agent_code=m.agent_code, supplier_code=m.supplier_code, ticket_carrier=m.ticket_carrier,
        **{f: int(getattr(m, f) or 0) for f in COUNT_FIELDS}, **{f: getattr(m, f) for f in RATE_FIELDS},
    )


def _replace_day(session: Session, day: date, rows: list[DataQualityRow]) -> int:
    """替换某天的数据：接口是该天的完整结果，先删后写，不留接口已不再返回的旧行。返回实际写入的行数。

    只写 stat_date 等于该天的行；接口返回了其他日期的行时丢弃并记警告，避免串到别的天的缓存里。
    """
    kept = [r for r in rows if r.stat_date == day]
    if len(kept) != len(rows):
        log.warning("数据质量接口查询 %s 时返回了 %d 行其他日期的数据，已忽略", day, len(rows) - len(kept))
    session.execute(delete(DataQualityStat).where(DataQualityStat.stat_date == day))
    upsert(session.connection(), DataQualityStat.__table__, [_to_record(r) for r in kept], KEY_COLS)
    return len(kept)


def ensure_days(session: Session, days: list[date], client: QualitySource | None = None, force: bool = False) -> None:
    """保证 days 中每一天的整天数据可用：历史缺的补拉，当天实时拉取。force=True 时历史日也重新拉取。"""
    if not days:
        return
    today = beijing_today()
    past = sorted({d for d in days if d < today})
    need_today = today in days
    if not past and not need_today:
        return

    own_client = client is None
    source: QualitySource = client or make_client()
    try:
        cached: set[date] = set()
        if past and not force:
            cached = set(session.scalars(select(DataQualitySync.stat_date).where(DataQualitySync.stat_date.in_(past))).all())
        for d in past:
            if d in cached:
                continue
            stored = _replace_day(session, d, source.fetch_day(d))
            session.merge(DataQualitySync(stat_date=d, row_count=stored, synced_at=datetime.now(timezone.utc)))
            session.commit()
        if need_today:
            _replace_day(session, today, source.fetch_day(today))
            session.commit()
    except (ErpAuthError, ErpUpstreamError) as exc:
        session.rollback()
        raise ImportValidationError(f"数据质量获取失败：{exc}") from exc
    finally:
        if own_client:
            source.close()


def load_rows(
    session: Session,
    day_from: date,
    day_to: date,
    period: tuple[time, time] | None = None,
    client: QualitySource | None = None,
) -> list[DataQualityRow]:
    """取 [day_from, day_to] 范围内的明细行。period 为 None 表示整天（走缓存），否则是每天的同一时段（实时调接口，不落库）。"""
    days = [day_from + timedelta(days=i) for i in range((day_to - day_from).days + 1)]
    if period is None:
        ensure_days(session, days, client)
        stored = session.scalars(
            select(DataQualityStat).where(DataQualityStat.stat_date.between(day_from, day_to)).order_by(DataQualityStat.id)
        ).all()
        return [_from_record(m) for m in stored]

    if len(days) > MAX_PERIOD_DAYS:
        raise ImportValidationError(f"按时间段查询最多 {MAX_PERIOD_DAYS} 天，请缩小日期范围")
    own_client = client is None
    source: QualitySource = client or make_client()
    rows: list[DataQualityRow] = []
    try:
        for d in days:
            rows.extend(source.fetch_range(*period_bounds(d, *period)))
    except (ErpAuthError, ErpUpstreamError) as exc:
        raise ImportValidationError(f"数据质量获取失败：{exc}") from exc
    finally:
        if own_client:
            source.close()
    return rows


def cached_range(session: Session) -> dict:
    """已缓存的整天日期范围，用于覆盖情况说明。"""
    from sqlalchemy import func

    lo, hi = session.execute(select(func.min(DataQualitySync.stat_date), func.max(DataQualitySync.stat_date))).one()
    return {"最早": str(lo) if lo else None, "最晚": str(hi) if hi else None}
