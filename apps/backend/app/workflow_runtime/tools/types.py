"""Workflow 工具生命周期共享的数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypedDict, TypeVar

from app.workflow_runtime.types import JsonObject

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
ResourceT = TypeVar("ResourceT")


@dataclass(frozen=True)
class ToolResources:
    """事务期间提供给领域 Tool 的不透明资源集合。"""

    values: tuple[object, ...] = ()

    def get(self, resource_type: type[ResourceT]) -> ResourceT | None:
        for value in self.values:
            if isinstance(value, resource_type):
                return value
        return None

    def require(self, resource_type: type[ResourceT], message: str) -> ResourceT:
        value = self.get(resource_type)
        if value is None:
            raise RuntimeError(message)
        return value


class ToolCall(TypedDict):
    tool_call_id: int
    index: int
    provider_id: str | None
    requested_by_model: bool
    name: str
    arguments: JsonObject
    status: ToolCallStatus
    interaction_payload: JsonObject | None
    should_execute: bool | None
    result: JsonObject | None
    replayed: bool


@dataclass(frozen=True)
class ToolCallIdentity:
    """不加载业务 Workflow 即可定位 Tool Call 所属执行。"""

    thread_id: int | str
    run_id: int | str


@dataclass(frozen=True)
class ToolContext:
    """工具执行所需的通用 Run 身份与不透明业务上下文。"""

    thread_id: int | str
    run_id: int | str
    subject: JsonObject
    scope: JsonObject
    workflow_context: JsonObject = field(default_factory=dict)
    tool_call_id: int | None = None
    resources: ToolResources = field(default_factory=ToolResources)


class ApprovalDecision(TypedDict):
    tool_call_id: int
    decision: ApprovalAction
    client_resolution_id: str


@dataclass(frozen=True)
class ToolResult:
    payload: JsonObject
    tool_call_id: int | None = None
    tool_name: str | None = None
    decision: ApprovalAction | None = None
    replayed: bool = False
