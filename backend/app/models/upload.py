from datetime import date, datetime, time

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, String, Time, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UploadBatch(Base):
    """每次上传一条记录。"""

    __tablename__ = "upload_batch"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    file_type: Mapped[str] = mapped_column(String(16))  # order / query / quality
    file_name: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    range_type: Mapped[str | None] = mapped_column(String(16))  # full_day / period，仅查询量、数据质量
    biz_date: Mapped[date | None] = mapped_column(Date)
    period_start: Mapped[time | None] = mapped_column(Time)
    period_end: Mapped[time | None] = mapped_column(Time)
    data_min_time: Mapped[datetime | None] = mapped_column(DateTime)  # 订单表：支付时间的最早值
    data_max_time: Mapped[datetime | None] = mapped_column(DateTime)  # 订单表：支付时间的最晚值
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict | None] = mapped_column(JSONB)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    user_id: Mapped[int | None] = mapped_column(BigInteger)  # 上传人，数据全员共享


class RawOrderRow(Base):
    """订单 Excel 原始行，用于追溯。已剔除乘机人姓名、票号、PNR、航司大编码。"""

    __tablename__ = "raw_order_row"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("upload_batch.id"), index=True)
    sheet_name: Mapped[str] = mapped_column(String(32))
    row_no: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSONB)
