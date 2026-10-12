from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChatLog(Base):
    """问答记录：问题、工具调用、回答，便于复核。"""

    __tablename__ = "chat_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    tool_calls: Mapped[list | None] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(String(64))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    user_id: Mapped[int | None] = mapped_column(Integer)  # 预留，Demo 不使用
