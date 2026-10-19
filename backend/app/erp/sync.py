"""每日自动同步查订比数据：北京时间每天 8 点拉取前一天的完整数据，用服务账号，归属服务账号的 UserId。

在后端进程内以守护线程运行。多个实例同时运行时，靠 PostgreSQL advisory lock 保证同一天只拉取一次。
"""

import logging
from datetime import datetime, time, timedelta
from threading import Thread
from time import sleep

from sqlalchemy import text

from app.core.db import SessionLocal
from app.erp.cache import BEIJING, beijing_today, ensure_days, make_service_client

log = logging.getLogger(__name__)

LOCK_KEY = 728301001  # advisory lock 的 key，仅用于查订比同步
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


def _run_forever() -> None:
    while True:
        wait = seconds_until_next_sync(datetime.now(BEIJING))
        log.info("查订比定时同步：%.0f 秒后（北京时间 8 点）拉取前一天数据", wait)
        sleep(wait)
        try:
            sync_day(beijing_today() - timedelta(days=1))
        except Exception:  # noqa: BLE001 - 定时任务不能因为一次失败退出
            log.exception("每日查订比同步失败")


def start_scheduler() -> None:
    Thread(target=_run_forever, name="query-sync", daemon=True).start()
