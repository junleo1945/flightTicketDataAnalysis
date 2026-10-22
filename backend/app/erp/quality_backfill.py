"""数据质量历史回填：对 [开始日期, 结束日期] 内缺失的整天逐日调用接口并缓存。

启动时由定时任务线程按 DATA_QUALITY_BACKFILL_FROM 自动执行（见 app.erp.sync）；也可手动执行，
用于临时补某段区间，或接口改口径后重拉（--force 会忽略已有的同步标记）：

    docker exec fa-backend python -m app.erp.quality_backfill 2026-09-22 2026-10-21 --force

- 幂等：只补 data_quality_sync 里没有的日期，已有的不重复请求。
- 某一天失败只记日志并继续下一天，最后汇总失败的日期。
- 结束日期最晚取到昨天；当天数据不缓存，由问答按需实时拉取。
"""

import logging
import sys
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import SessionLocal
from app.erp.cache import beijing_today
from app.erp.quality_cache import QualitySource, ensure_days, make_client
from app.importers.common import ImportValidationError
from app.models.analysis import DataQualitySync

log = logging.getLogger(__name__)


def pending_days(start: date, end: date, synced: set[date], today: date, force: bool = False) -> list[date]:
    """需要补的日期：[start, min(end, 昨天)] 中没有同步标记的天（force 时全部）。"""
    last = min(end, today - timedelta(days=1))
    if start > last:
        return []
    days = [start + timedelta(days=i) for i in range((last - start).days + 1)]
    return days if force else [d for d in days if d not in synced]


def backfill(
    start: date,
    end: date,
    force: bool = False,
    client: QualitySource | None = None,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> dict:
    own_client = client is None
    source: QualitySource = client or make_client()
    done: list[date] = []
    failed: dict[date, str] = {}
    try:
        with session_factory() as session:
            synced = set(session.scalars(select(DataQualitySync.stat_date).where(DataQualitySync.stat_date.between(start, end))).all())
            days = pending_days(start, end, synced, beijing_today(), force)
            for d in days:
                try:
                    ensure_days(session, [d], source, force=force)
                    done.append(d)
                except ImportValidationError as exc:
                    log.warning("数据质量回填 %s 失败：%s", d, exc)
                    failed[d] = str(exc)
    finally:
        if own_client:
            source.close()
    return {"待补天数": len(done) + len(failed), "成功": len(done), "失败": {str(d): m for d, m in failed.items()}}


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    force = "--force" in argv
    if len(args) != 2:
        print(__doc__)
        return 1
    start, end = date.fromisoformat(args[0]), date.fromisoformat(args[1])
    if start > end:
        print("开始日期不能晚于结束日期", file=sys.stderr)
        return 1
    try:
        print(backfill(start, end, force))
    except ImportValidationError as exc:
        print(f"回填失败：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main(sys.argv))
