"""工具各层共享的最小数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, TypedDict

from app.ai_chat.types import JsonObject

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


ToolCallStatus = Literal[
    "received",
    "validated",
    "awaiting_approval",
    "awaiting_input",
    "approved",
    "executing",
    "resolved",
]
ApprovalAction = Literal["approve", "reject"]


class ToolCall(TypedDict):
    """图中唯一的工具调用结构，生命周期由状态字段表达。"""

    tool_call_id: int
    index: int
    provider_id: str | None
    name: str
    arguments: JsonObject
    status: ToolCallStatus
    security: Literal["low", "medium", "high"]
    proposal_payload: JsonObject | None
    should_execute: bool | None
    result: JsonObject | None
    replayed: bool


@dataclass(frozen=True)
class ToolContext:
    """服务提供给处理器的可信身份与事务绑定。"""

    conversation_id: int
    run_id: int
    subject: JsonObject
    scope: JsonObject
    adapter_context: JsonObject = field(default_factory=dict)
    tool_call_id: int | None = None
    session: AsyncSession | None = None


class ApprovalDecision(TypedDict):
    """用户提交的审批决定及其幂等标识。"""

    tool_call_id: int
    decision: ApprovalAction
    client_resolution_id: str


@dataclass(frozen=True)
class ToolResult:
    """处理器结果以及服务补充的持久化调用身份。"""

    payload: JsonObject
    tool_call_id: int | None = None
    tool_name: str | None = None
    decision: ApprovalAction | None = None
    replayed: bool = False
