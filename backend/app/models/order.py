from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TmOrder(Base):
    """渠道订单：一个途灵订单一行。金额为整单金额（采购维度汇总）。"""

    __tablename__ = "tm_order"

    tm_order_no: Mapped[str] = mapped_column(String(32), primary_key=True)
    channel_code: Mapped[str] = mapped_column(String(32), index=True)
    customer_order_no: Mapped[str | None] = mapped_column(String(64))
    order_type: Mapped[str | None] = mapped_column(String(16))
    order_status: Mapped[str] = mapped_column(String(32), index=True)
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    pay_time: Mapped[datetime | None] = mapped_column(DateTime)
    pay_date: Mapped[date | None] = mapped_column(Date, index=True)
    ticket_time: Mapped[datetime | None] = mapped_column(DateTime)
    currency: Mapped[str | None] = mapped_column(String(8))
    total_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    rate_cny: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    total_cny: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    rate_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    total_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    pay_method: Mapped[str | None] = mapped_column(String(32))
    depart_time: Mapped[datetime | None] = mapped_column(DateTime)
    segment_count: Mapped[int | None] = mapped_column(Integer)
    passenger_count: Mapped[int] = mapped_column(Integer)
    airline_codes: Mapped[str | None] = mapped_column(String(128))
    batch_id: Mapped[int] = mapped_column(ForeignKey("upload_batch.id"), index=True)


class SupplierOrder(Base):
    """供应订单：一个 TM供应商订单号一行（部分供应没有供应商订单号）。"""

    __tablename__ = "supplier_order"

    tm_supplier_order_no: Mapped[str] = mapped_column(String(40), primary_key=True)
    tm_order_no: Mapped[str] = mapped_column(ForeignKey("tm_order.tm_order_no"), index=True)
    supplier_order_no: Mapped[str | None] = mapped_column(String(64))
    supplier_code: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    create_time: Mapped[datetime | None] = mapped_column(DateTime)
    pay_time: Mapped[datetime | None] = mapped_column(DateTime)  # 原始供应商订单支付时间
    pay_time_effective: Mapped[datetime | None] = mapped_column(DateTime, index=True)  # 按 Q16 兜底后的时间
    pay_time_source: Mapped[str | None] = mapped_column(String(16))  # supplier_pay / tm_pay / ticket
    ticket_time: Mapped[datetime | None] = mapped_column(DateTime)
    currency: Mapped[str | None] = mapped_column(String(8))
    supplier_total: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    supplier_total_cny: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    supplier_total_usd: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    rate_cny: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    rate_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    passenger_count: Mapped[int] = mapped_column(Integer)
    batch_id: Mapped[int] = mapped_column(ForeignKey("upload_batch.id"), index=True)


class OrderAirline(Base):
    """渠道订单的航司（一单多航司时各一行）。"""

    __tablename__ = "order_airline"

    tm_order_no: Mapped[str] = mapped_column(ForeignKey("tm_order.tm_order_no"), primary_key=True)
    airline_code: Mapped[str] = mapped_column(String(8), primary_key=True)


class OrderRoute(Base):
    """渠道订单的航线（行程按逗号拆段，每段一行）。"""

    __tablename__ = "order_route"

    tm_order_no: Mapped[str] = mapped_column(ForeignKey("tm_order.tm_order_no"), primary_key=True)
    route: Mapped[str] = mapped_column(String(16), primary_key=True)
