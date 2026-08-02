"""返回不透明结果的业务工具处理器协议。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from app.ai_chat.types import JsonObject


@dataclass(frozen=True)
class ToolContext:
    """提供给工具业务逻辑的标识符和不透明绑定。"""

    conversation_id: int
    run_id: int
    tool_call_id: int | None
    subject: JsonObject
    target: JsonObject
    adapter_context: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalProposal:
    """执行前必须审批的不透明提案。"""

    proposal_payload: JsonObject
    guard_payload: JsonObject


@dataclass(frozen=True)
class ImmediateToolResult:
    """无需用户审批的不透明工具结果。"""

    payload: JsonObject


@dataclass(frozen=True)
class ToolResult:
    """审批决定后产生的不透明结果。"""

    payload: JsonObject


ToolValidation = ApprovalProposal | ImmediateToolResult


class ToolHandler(ABC):
    """校验并处理一个业务工具，同时不向通用层泄露其语义。"""

    name: str
    description: str
    arguments_schema: type[BaseModel]

    @abstractmethod
    async def invoke(self, context: ToolContext, arguments: BaseModel) -> ToolValidation:
        """返回审批提案或立即生效的不透明结果。"""

    @abstractmethod
    async def resolve(
        self,
        context: ToolContext,
        arguments: BaseModel,
        proposal_payload: JsonObject,
        guard_payload: JsonObject,
        decision: Literal["approve", "reject"],
    ) -> ToolResult:
        """通过业务逻辑处理已同意或已拒绝的提案。"""

    def schema(self) -> dict[str, Any]:
        """返回与模型提供方无关的工具 JSON Schema。"""
        return self.arguments_schema.model_json_schema()
