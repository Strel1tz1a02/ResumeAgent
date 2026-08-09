"""Tool 调用在校验、执行和延迟投递阶段使用的结果类型。"""

from dataclasses import dataclass
from typing import Literal, TypedDict

from app.ai_chat.types import JsonObject
from app.ai_chat.tools.security import ToolSecurity


class PendingToolResult(TypedDict):
    """等待模型完整响应的不透明 Tool Result。"""

    tool_call_id: int
    provider_tool_call_id: str | None
    tool_name: str
    arguments: JsonObject
    result: JsonObject


@dataclass(frozen=True)
class ValidatedToolCall:
    """Handler 校验后生成的可信执行依据。"""

    proposal_payload: JsonObject
    guard_payload: JsonObject


@dataclass(frozen=True)
class ToolResult:
    """Tool 的稳定业务结果。"""

    payload: JsonObject


ToolValidationResult = ValidatedToolCall | ToolResult


@dataclass(frozen=True)
class PreparedToolCall:
    """已完成校验、等待 Graph 风险策略判断的工具调用。"""

    tool_call_id: int
    tool_name: str
    security: ToolSecurity


@dataclass(frozen=True)
class ApprovalRequest:
    """已持久化、等待审批决定的提案。"""

    tool_call_id: int
    tool_name: str
    proposal_payload: JsonObject


@dataclass(frozen=True)
class ApprovedToolCall:
    """已持久化批准决定、等待执行的工具调用。"""

    tool_call_id: int
    tool_name: str
    client_resolution_id: str


@dataclass(frozen=True)
class CompletedToolCall:
    """已持久化完成的工具调用结果。"""

    tool_call_id: int
    tool_name: str
    result: JsonObject
    decision: Literal["approve", "reject"] | None
    replayed: bool


ToolCallState = (
    PreparedToolCall | ApprovalRequest | ApprovedToolCall | CompletedToolCall
)
