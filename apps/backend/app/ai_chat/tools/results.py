"""Tool 调用在校验、审批和延迟投递阶段使用的结果类型。"""

from dataclasses import dataclass
from typing import TypedDict

from app.ai_chat.types import JsonObject


class PendingToolResult(TypedDict):
    """等待模型完整响应的不透明 Tool Result。"""

    tool_call_id: int
    provider_tool_call_id: str | None
    tool_name: str
    arguments: JsonObject
    result: JsonObject


@dataclass(frozen=True)
class ApprovalProposal:
    """执行前必须审批的不透明提案。"""

    proposal_payload: JsonObject
    guard_payload: JsonObject


@dataclass(frozen=True)
class ToolResult:
    """无需审批或审批决定后产生的不透明 Tool Result。"""

    payload: JsonObject


ToolInvocationResult = ApprovalProposal | ToolResult
