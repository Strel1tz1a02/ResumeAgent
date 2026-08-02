"""可复用 AI 对话运行时拥有的 SQLAlchemy 模型。"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


def utcnow_iso() -> str:
    """返回 UTC ISO-8601 审计时间戳。"""
    return datetime.now(timezone.utc).isoformat()


class AiChatConversation(Base):
    """一个绑定业务对象的 AI 会话。"""

    __tablename__ = "ai_chat_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    adapter: Mapped[str] = mapped_column(String(160), index=True)
    subject: Mapped[dict[str, Any]] = mapped_column(JSON)
    target: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    language: Mapped[str] = mapped_column(String(8), default="zh")
    end_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
    ended_at: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'ended')", name="ck_ai_chat_conversation_status"
        ),
    )


class AiChatRun(Base):
    """一次开场、用户轮次或审批后续跑。"""

    __tablename__ = "ai_chat_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("ai_chat_conversations.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    tools_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    started_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('opening', 'user_turn', 'post_tool_continuation')",
            name="ck_ai_chat_run_kind",
        ),
        CheckConstraint(
            "status IN ('running', 'suspended', 'completed', 'failed', 'cancelled')",
            name="ck_ai_chat_run_status",
        ),
        Index(
            "ux_ai_chat_runs_current_conversation",
            "conversation_id",
            unique=True,
            sqlite_where=text("status IN ('running', 'suspended')"),
        ),
    )


class AiChatMessage(Base):
    """一条可见的用户或助手消息。"""

    __tablename__ = "ai_chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("ai_chat_conversations.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_chat_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="completed", index=True)
    client_message_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=utcnow_iso)

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')", name="ck_ai_chat_message_role"
        ),
        CheckConstraint(
            "status IN ('generating', 'completed', 'failed', 'cancelled')",
            name="ck_ai_chat_message_status",
        ),
        UniqueConstraint(
            "conversation_id", "sequence", name="uq_ai_chat_message_sequence"
        ),
        UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_ai_chat_message_client_id",
        ),
    )


class AiChatToolCall(Base):
    """持久化的工具调用，以及可选的提案、决定和工具结果。"""

    __tablename__ = "ai_chat_tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("ai_chat_conversations.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[int] = mapped_column(
        ForeignKey("ai_chat_runs.id", ondelete="CASCADE"), index=True
    )
    provider_tool_call_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(160))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON)
    proposal_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    guard_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="received", index=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tool_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    delivery_status: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    client_resolution_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    resolved_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=utcnow_iso)

    __table_args__ = (
        CheckConstraint(
            "status IN ('received', 'awaiting_approval', 'resolved')",
            name="ck_ai_chat_tool_status",
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN ('approve', 'reject')",
            name="ck_ai_chat_tool_decision",
        ),
        CheckConstraint(
            "delivery_status IS NULL OR delivery_status IN ('pending', 'consumed')",
            name="ck_ai_chat_tool_delivery",
        ),
        Index(
            "ux_ai_chat_tool_provider_call",
            "run_id",
            "provider_tool_call_id",
            unique=True,
            sqlite_where=text("provider_tool_call_id IS NOT NULL"),
        ),
        UniqueConstraint(
            "conversation_id",
            "client_resolution_id",
            name="uq_ai_chat_tool_resolution_id",
        ),
    )
