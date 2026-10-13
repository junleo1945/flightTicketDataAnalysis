from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DataGrant(Base):
    """数据授权：owner 的数据对 grantee 只读可见（指标查询、问答）。
    数据行本身仍只归上传人 / ERP 会话所属用户，授权只影响读取范围，撤销即时生效。"""

    __tablename__ = "data_grant"
    __table_args__ = (UniqueConstraint("owner_user_id", "grantee_user_id", name="uq_data_grant_owner_grantee"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    grantee_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
