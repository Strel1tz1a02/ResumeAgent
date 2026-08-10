"""AI Chat 会话记忆的持久化模型。"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class AiChatRunMemory(Base):
    """一个终态 Run 对应的压缩状态与累计记忆快照。"""

    __tablename__ = "ai_chat_run_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("ai_chat_runs.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    core: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    other: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    memory_token_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_ai_chat_run_memory_status",
        ),
    )
