"""AI Chat 会话记忆的持久化模型。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.ai_chat.models.models import utcnow_iso
from app.models import Base


class AiChatRunMemory(Base):
    """一个终态 Run 对应的压缩状态与累计记忆快照。"""

    __tablename__ = "ai_chat_run_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("ai_chat_conversations.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[int] = mapped_column(
        ForeignKey("ai_chat_runs.id", ondelete="CASCADE"), unique=True
    )
    parent_memory_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_chat_run_memories.id", ondelete="CASCADE"), nullable=True
    )
    source_bundle_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    operations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    core: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    other: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    memory_token_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=utcnow_iso)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_ai_chat_run_memory_status",
        ),
        UniqueConstraint(
            "conversation_id",
            "parent_memory_id",
            name="uq_ai_chat_run_memory_parent",
        ),
        Index(
            "ix_ai_chat_run_memory_conversation_status",
            "conversation_id",
            "status",
        ),
    )
