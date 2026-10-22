from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, Numeric, String, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class QueryStat(Base):
    """查询量表。同一「日期 + 范围 + 合作方」再次上传时覆盖。"""

    __tablename__ = "query_stat"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "biz_date", "range_type", "period_start", "period_end", "partner_type", "partner_code", name="uq_query_stat_key"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, server_default="0")  # 数据归属：上传人或拉取人；0 表示无归属，不对任何人可见
    biz_date: Mapped[date] = mapped_column(Date, index=True)
    range_type: Mapped[str] = mapped_column(String(16))
    period_start: Mapped[time] = mapped_column(Time)
    period_end: Mapped[time] = mapped_column(Time)
    partner_type: Mapped[str] = mapped_column(String(16))  # supplier / channel
    partner_code: Mapped[str] = mapped_column(String(64))
    query_count: Mapped[int] = mapped_column(BigInteger, default=0)
    quote_count: Mapped[int] = mapped_column(BigInteger, default=0)
    order_count: Mapped[int] = mapped_column(BigInteger, default=0)
    segment_count: Mapped[int] = mapped_column(BigInteger, default=0)
    blocked_count: Mapped[int] = mapped_column(BigInteger, default=0)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("upload_batch.id"), index=True)  # 来自接口时为空
    source: Mapped[str] = mapped_column(String(16), default="excel", server_default="excel")  # excel / erp_api


class DataQualityStat(Base):
    """验价生单数据质量（来自接口，整天，全局一份，不按用户隔离）。"""

    __tablename__ = "data_quality_stat"
    __table_args__ = (
        UniqueConstraint("stat_date", "agent_code", "supplier_code", "ticket_carrier", name="uq_data_quality_stat_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stat_date: Mapped[date] = mapped_column(Date, index=True)
    agent_code: Mapped[str] = mapped_column(String(64))  # 渠道（客户）
    supplier_code: Mapped[str] = mapped_column(String(64))
    ticket_carrier: Mapped[str] = mapped_column(String(8))  # 航司
    total: Mapped[int] = mapped_column(BigInteger, default=0)
    timeout_5_10: Mapped[int] = mapped_column(BigInteger, default=0)
    timeout_gt_10: Mapped[int] = mapped_column(BigInteger, default=0)
    verify_success: Mapped[int] = mapped_column(BigInteger, default=0)
    verify_fail: Mapped[int] = mapped_column(BigInteger, default=0)
    supplier_success: Mapped[int] = mapped_column(BigInteger, default=0)
    supplier_fail: Mapped[int] = mapped_column(BigInteger, default=0)
    supplier_changed: Mapped[int] = mapped_column(BigInteger, default=0)
    unbook: Mapped[int] = mapped_column(BigInteger, default=0)
    booking_success: Mapped[int] = mapped_column(BigInteger, default=0)
    booking_fail: Mapped[int] = mapped_column(BigInteger, default=0)
    # 以下三个是接口给的比率，只用于与反算值对照，分析一律用数量字段重新计算
    booking_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=0)
    booking_success_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=0)
    timeout_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=0)


class DataQualitySync(Base):
    """数据质量历史整天的缓存标记：某天已从接口拉取并写入 data_quality_stat。当天不记录，每次实时拉取。"""

    __tablename__ = "data_quality_sync"

    stat_date: Mapped[date] = mapped_column(Date, primary_key=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CommissionRule(Base):
    """佣金配置，调整规则不需要改代码。"""

    __tablename__ = "commission_rule"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel_code: Mapped[str] = mapped_column(String(32), index=True)
    rule_type: Mapped[str] = mapped_column(String(16))  # percent / fixed_usd
    value: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)


class Look2BookSync(Base):
    """查订比接口的按日缓存记录：某一天（历史整天）已从 ERP 拉取并写入 query_stat。当天不记录，每次实时拉取。"""

    __tablename__ = "look2book_sync"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    biz_date: Mapped[date] = mapped_column(Date, primary_key=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
