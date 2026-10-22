"""每日自动同步：北京时间每天 8 点拉取前一天的完整数据。

- 查订比：用服务账号，归属服务账号的 UserId。
- 数据质量：接口免鉴权，数据全局共享，不需要账号。
启动时还会按 DATA_QUALITY_BACKFILL_FROM 补齐数据质量历史数据（只补缺的天）。

在后端进程内以守护线程运行。多个实例同时运行时，靠 PostgreSQL advisory lock 保证同一件事只执行一次。
"""

import logging
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from threading import Thread
from time import sleep

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.erp.cache import BEIJING, beijing_today, ensure_days, make_service_client
from app.erp.quality_backfill import backfill
from app.erp.quality_cache import ensure_days as ensure_quality_days

log = logging.getLogger(__name__)

LOCK_KEY = 728301001  # advisory lock 的 key，仅用于查订比同步
QUALITY_LOCK_KEY = 728301002  # 数据质量的同步与回填共用
SYNC_TIME = time(8, 0)  # 北京时间；0 点时 ERP 还没出完前一天的统计，会拉到空数据


def seconds_until_next_sync(now: datetime) -> float:
    """距离下一个北京时间 8 点的秒数（已过 8 点则算明天）。now 必须带时区。"""
    nxt = datetime.combine(now.date(), SYNC_TIME, tzinfo=BEIJING)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


def sync_day(day) -> bool:
    """拉取某一天的查订比数据（服务账号）。若其他实例正在同步则跳过，返回是否执行。"""
    client, service_uid = make_service_client()
    try:
        with SessionLocal() as session:
            locked = session.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
            if not locked:
                log.info("查订比同步已在其他实例执行，跳过 %s", day)
                return False
            try:
                ensure_days(session, [day], service_uid, "", client=client)
            finally:
                # 连接会回到连接池，必须确保释放锁；出错时先回滚，否则解锁语句会失败
                session.rollback()
                session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
                session.commit()
        log.info("查订比同步完成：%s", day)
        return True
    finally:
        client.close()


@contextmanager
def advisory_lock(key: int):
    """独占一个数据库连接持有 advisory lock，期间其他会话的提交、回滚不会影响锁。产出是否拿到锁。"""
    with engine.connect() as conn:
        locked = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar())
        conn.commit()
        try:
            yield locked
        finally:
            if locked:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                conn.commit()


def sync_quality_day(day: date) -> bool:
    """重新拉取某一天的数据质量（覆盖已有缓存，避免早先按需拉到的不完整数据留下来）。其他实例正在执行则跳过。"""
    with advisory_lock(QUALITY_LOCK_KEY) as locked:
        if not locked:
            log.info("数据质量同步已在其他实例执行，跳过 %s", day)
            return False
        with SessionLocal() as session:
            ensure_quality_days(session, [day], force=True)
    log.info("数据质量同步完成：%s", day)
    return True


def run_startup_backfill() -> None:
    """启动时补齐数据质量历史数据（DATA_QUALITY_BACKFILL_FROM 到昨天），失败只记日志。"""
    settings = get_settings()
    raw = settings.data_quality_backfill_from.strip()
    if not raw:
        return
    if not settings.data_quality_base_url.strip():
        log.warning("已配置 DATA_QUALITY_BACKFILL_FROM 但未配置 DATA_QUALITY_BASE_URL，跳过数据质量回填")
        return
    try:
        start = date.fromisoformat(raw)
    except ValueError:
        log.error("DATA_QUALITY_BACKFILL_FROM 格式应为 YYYY-MM-DD：%s", raw)
        return
    end = beijing_today() - timedelta(days=1)
    if start > end:
        return
    try:
        with advisory_lock(QUALITY_LOCK_KEY) as locked:
            if not locked:
                log.info("数据质量回填已在其他实例执行，跳过")
                return
            result = backfill(start, end)
        log.info("数据质量回填完成：%s ～ %s，%s", start, end, result)
    except Exception:  # noqa: BLE001 - 回填失败不能影响服务
        log.exception("数据质量回填失败")


def _run_forever() -> None:
    run_startup_backfill()
    while True:
        wait = seconds_until_next_sync(datetime.now(BEIJING))
        log.info("定时同步：%.0f 秒后（北京时间 8 点）拉取前一天数据", wait)
        sleep(wait)
        yesterday = beijing_today() - timedelta(days=1)
        for name, task in (("查订比", sync_day), ("数据质量", sync_quality_day)):
            try:
                task(yesterday)
            except Exception:  # noqa: BLE001 - 定时任务不能因为一次失败退出，一项失败也不影响另一项
                log.exception("每日%s同步失败", name)


def start_scheduler() -> None:
    Thread(target=_run_forever, name="query-sync", daemon=True).start()
