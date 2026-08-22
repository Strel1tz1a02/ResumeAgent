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
    scope: Mapped[dict[str, Any]] = mapped_column(JSON)
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
    """一次开场或用户消息运行。"""

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
            "kind IN ('opening', 'user_turn')",
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
    """持久化工具调用，以及可选的交互载荷、决定和工具结果。"""

    __tablename__ = "ai_chat_tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("ai_chat_conversations.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[int] = mapped_column(
        ForeignKey("ai_chat_runs.id", ondelete="CASCADE"), index=True
    )
    tool_call_index: Mapped[int] = mapped_column(Integer)
    provider_tool_call_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    requested_by_model: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    tool_name: Mapped[str] = mapped_column(String(160))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON)
    interaction_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
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
        # 工具调用只能处于状态机声明的状态中。
        CheckConstraint(
            "status IN ('received', 'validated', 'awaiting_approval', 'awaiting_input', "
            "'approved', 'executing', 'resolved')",
            name="ck_ai_chat_tool_status",
        ),
        # 已完成的工具调用必须同时持久化执行结果。
        CheckConstraint(
            "status != 'resolved' OR tool_result IS NOT NULL",
            name="ck_ai_chat_tool_result",
        ),
        # 审批决定为空表示尚未处理，否则只能是批准或拒绝。
        CheckConstraint(
            "decision IS NULL OR decision IN ('approve', 'reject')",
            name="ck_ai_chat_tool_decision",
        ),
        # 工具结果尚未产生时投递状态为空，产生后只能是待投递或已消费。
        CheckConstraint(
            "delivery_status IS NULL OR delivery_status IN ('pending', 'consumed')",
            name="ck_ai_chat_tool_delivery",
        ),
        # 同一次运行中的工具调用序号唯一，用于保证重放时不会重复固化。
        Index(
            "ux_ai_chat_tool_run_index",
            "run_id",
            "tool_call_index",
            unique=True,
        ),
        # 模型供应商提供调用 ID 时，同一次运行中不允许重复。
        Index(
            "ux_ai_chat_tool_provider_call",
            "run_id",
            "provider_tool_call_id",
            unique=True,
            sqlite_where=text("provider_tool_call_id IS NOT NULL"),
        ),
        # 同一会话中的客户端审批请求 ID 唯一，用于保证审批操作幂等。
        UniqueConstraint(
            "conversation_id",
            "client_resolution_id",
            name="uq_ai_chat_tool_resolution_id",
        ),
    )
