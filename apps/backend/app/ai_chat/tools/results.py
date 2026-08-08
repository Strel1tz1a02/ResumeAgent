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

# Temporary aliases keep staged callers import-safe until Task 5/7 removes the
# legacy invoke/resolve protocol.
ApprovalProposal = ValidatedToolCall
ToolInvocationResult = ToolValidationResult


@dataclass(frozen=True)
class PreparedToolCall:
    """Validated Tool Call ready for Graph risk policy."""

    tool_call_id: int
    tool_name: str
    security: ToolSecurity


@dataclass(frozen=True)
class ApprovalRequest:
    """Persisted proposal awaiting an approval decision."""

    tool_call_id: int
    tool_name: str
    proposal_payload: JsonObject


@dataclass(frozen=True)
class ApprovedToolCall:
    """Persisted approval ready for execution."""

    tool_call_id: int
    tool_name: str
    client_resolution_id: str


@dataclass(frozen=True)
class CompletedToolCall:
    """Durably resolved Tool Call result."""

    tool_call_id: int
    tool_name: str
    result: JsonObject
    decision: Literal["approve", "reject"] | None
    replayed: bool


ToolCallState = (
    PreparedToolCall | ApprovalRequest | ApprovedToolCall | CompletedToolCall
)
