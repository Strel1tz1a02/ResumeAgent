"""Conversation-memory persistence models owned by the memory module."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.ai_chat.models.models import utcnow_iso
from app.models import Base


class AiChatConversationMemorySnapshot(Base):
    """An immutable cumulative memory snapshot."""

    __tablename__ = "ai_chat_conversation_memory_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("ai_chat_conversations.id", ondelete="CASCADE"), index=True
    )
    parent_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_chat_conversation_memory_snapshots.id", ondelete="CASCADE"),
        nullable=True,
    )
    source_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_chat_runs.id", ondelete="CASCADE"), nullable=True
    )
    source_bundle_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    covered_through_sequence: Mapped[int] = mapped_column(Integer, default=0)
    operations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    core: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    other: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    memory_token_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)

    __table_args__ = (
        CheckConstraint(
            "(parent_snapshot_id IS NULL AND source_run_id IS NULL "
            "AND source_bundle_hash IS NULL) OR "
            "(source_run_id IS NOT NULL AND source_bundle_hash IS NOT NULL)",
            name="ck_ai_chat_memory_snapshot_source",
        ),
        UniqueConstraint(
            "conversation_id",
            "parent_snapshot_id",
            name="uq_ai_chat_memory_snapshot_parent",
        ),
        UniqueConstraint(
            "conversation_id",
            "source_run_id",
            name="uq_ai_chat_memory_snapshot_source_run",
        ),
    )


class AiChatConversationMemory(Base):
    """The active snapshot pointer and compaction lease for a conversation."""

    __tablename__ = "ai_chat_conversation_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("ai_chat_conversations.id", ondelete="CASCADE"), unique=True
    )
    active_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("ai_chat_conversation_memory_snapshots.id", ondelete="RESTRICT")
    )
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
