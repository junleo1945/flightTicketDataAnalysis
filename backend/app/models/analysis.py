from datetime import date, time
from decimal import Decimal

from sqlalchemy import BigInteger, Date, ForeignKey, Integer, Numeric, String, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class QueryStat(Base):
    """查询量表。同一「日期 + 范围 + 合作方」再次上传时覆盖。"""

    __tablename__ = "query_stat"
    __table_args__ = (
        UniqueConstraint("biz_date", "range_type", "period_start", "period_end", "partner_type", "partner_code", name="uq_query_stat_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
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
    batch_id: Mapped[int] = mapped_column(ForeignKey("upload_batch.id"), index=True)


class SupplyQuality(Base):
    """数据质量表（两种格式共用，供应版的 agent_code 为空串）。"""

    __tablename__ = "supply_quality"
    __table_args__ = (
        UniqueConstraint(
            "biz_date", "range_type", "period_start", "period_end", "airline_code", "agent_code", "supplier_code",
            name="uq_supply_quality_key",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    biz_date: Mapped[date] = mapped_column(Date, index=True)
    range_type: Mapped[str] = mapped_column(String(16))
    period_start: Mapped[time] = mapped_column(Time)
    period_end: Mapped[time] = mapped_column(Time)
    airline_code: Mapped[str] = mapped_column(String(8))
    agent_code: Mapped[str] = mapped_column(String(64), default="")
    supplier_code: Mapped[str] = mapped_column(String(64))
    verify_total: Mapped[int] = mapped_column(BigInteger, default=0)
    timeout_5s: Mapped[int] = mapped_column(BigInteger, default=0)
    timeout_10s: Mapped[int] = mapped_column(BigInteger, default=0)
    verify_success: Mapped[int] = mapped_column(BigInteger, default=0)
    verify_fail: Mapped[int] = mapped_column(BigInteger, default=0)
    supplier_success: Mapped[int] = mapped_column(BigInteger, default=0)
    supplier_fail: Mapped[int] = mapped_column(BigInteger, default=0)
    price_change: Mapped[int | None] = mapped_column(BigInteger)
    not_booked: Mapped[int] = mapped_column(BigInteger, default=0)
    book_success: Mapped[int] = mapped_column(BigInteger, default=0)
    book_fail: Mapped[int] = mapped_column(BigInteger, default=0)
    book_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=0)
    timeout_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=0)
    batch_id: Mapped[int] = mapped_column(ForeignKey("upload_batch.id"), index=True)


class CommissionRule(Base):
    """佣金配置，调整规则不需要改代码。"""

    __tablename__ = "commission_rule"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel_code: Mapped[str] = mapped_column(String(32), index=True)
    rule_type: Mapped[str] = mapped_column(String(16))  # percent / fixed_usd
    value: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
