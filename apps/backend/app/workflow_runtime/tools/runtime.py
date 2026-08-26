"""Workflow Graph 使用的显式工具能力端口。"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Protocol

from langchain_core.messages import ToolCall as LangChainToolCall
from langchain_core.tools import BaseTool

from app.workflow_runtime.tools.approval import ToolApprovalPolicy
from app.workflow_runtime.tools.operation import RegisteredTool
from app.workflow_runtime.tools.types import (
    ApprovalDecision,
    ToolCall,
    ToolCallIdentity,
    ToolContext,
    ToolResources,
    ToolResult,
)
from app.workflow_runtime.types import JsonObject


class ApprovalRouter(Protocol):
    def route(self, call: ToolCall) -> str: ...


class ModelToolSet(Protocol):
    """模型调用只需要看到的工具 Schema 视图。"""

    @property
    def model_tools(self) -> Mapping[str, BaseTool]: ...


class ToolLifecycle(ModelToolSet, Protocol):
    """Workflow 节点需要的完整工具调用生命周期。"""

    tools: Mapping[str, RegisteredTool]
    approval_policy: ToolApprovalPolicy

    @property
    def approval(self) -> ApprovalRouter: ...

    def bind_tools(
        self,
        tools: Mapping[str, RegisteredTool],
        approval_policy: ToolApprovalPolicy,
    ) -> ToolLifecycle: ...

    async def pending_results(self, thread_id: int | str) -> list[ToolCall]: ...

    async def locate_call(self, tool_call_id: int) -> ToolCallIdentity: ...

    async def consume_results(
        self,
        tool_call_ids: Collection[int],
        *,
        resources: ToolResources | None = None,
    ) -> None: ...

    async def get_call(self, tool_call_id: int) -> ToolCall: ...

    async def validate_call(
        self,
        context: ToolContext,
        model_call: LangChainToolCall,
        *,
        index: int = 0,
    ) -> ToolCall: ...

    async def validate_system_call(
        self,
        context: ToolContext,
        *,
        identity: str,
        name: str,
        arguments: JsonObject,
        requested_by_model: bool = False,
    ) -> ToolCall: ...

    async def validate_model_call_as(
        self,
        context: ToolContext,
        model_call: LangChainToolCall,
        *,
        identity: str,
        expected_name: str,
    ) -> ToolCall: ...

    async def request_approval(self, tool_call_id: int) -> ToolCall: ...
    async def record_decision(self, approval: ApprovalDecision) -> ToolCall: ...
    async def request_input(self, tool_call_id: int) -> ToolCall: ...

    async def resolve_input(
        self,
        tool_call_id: int,
        client_resolution_id: str,
        payload: JsonObject,
    ) -> ToolResult: ...

    async def consume_result(self, tool_call_id: int) -> None: ...

    async def execute_call(
        self,
        context: ToolContext,
        tool_call_id: int,
    ) -> ToolResult: ...
