"""提供给业务图节点的适配器级执行环境。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.streaming.model import AiChatModel, ModelStreamEvent
from app.ai_chat.tools.buffer import AssembledToolCall
from app.ai_chat.tools.handler import ToolContext, ToolHandler
from app.ai_chat.tools.results import (
    ApprovalRequest,
    ApprovedToolCall,
    CompletedToolCall,
    PreparedToolCall,
)
from app.ai_chat.types import JsonObject

if TYPE_CHECKING:
    from app.ai_chat.services.tool_call_service import ToolCallService
    from app.ai_chat.tools.lifecycle import ToolDispatch


@dataclass(frozen=True)
class AiChatRuntime:
    """一个具体适配器所需的无状态模型与统一 Tool 服务。"""

    model: AiChatModel
    tools: ToolCallService

    def bind_tools(
        self,
        tool_handlers: Mapping[str, ToolHandler],
    ) -> AiChatRuntime:
        """返回绑定指定工具集合的运行环境。"""
        return AiChatRuntime(
            model=self.model,
            tools=self.tools.bind_handlers(tool_handlers),
        )

    async def stream_model(
        self,
        *,
        messages: list[JsonObject],
        tools_enabled: bool,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ModelStreamEvent]:
        """在执行本次工具策略的同时流式调用模型。"""
        async for event in self.model.stream(
            messages=messages,
            handlers=self.tools.model_handlers,
            tools_enabled=tools_enabled,
            max_tokens=max_tokens,
        ):
            yield event

    async def receive_tool_call(
        self,
        *,
        context: ToolContext,
        call: AssembledToolCall,
    ) -> ToolDispatch:
        """临时把 Service 状态映射给 Task 6 前的旧 Graph。"""
        from app.ai_chat.tools.lifecycle import ApprovalRequired, ToolCompleted

        state = await self.tools.validate_call(context, call)
        if isinstance(state, PreparedToolCall):
            state = await self.tools.request_approval(state.tool_call_id)
        if isinstance(state, ApprovalRequest):
            return ApprovalRequired(
                tool_call_id=state.tool_call_id,
                proposal_payload=state.proposal_payload,
            )
        if isinstance(state, ApprovedToolCall):
            state = await self.tools.execute_call(context, state.tool_call_id)
        if isinstance(state, CompletedToolCall):
            return ToolCompleted(
                tool_call_id=state.tool_call_id,
                result=state.result,
            )
        raise ToolProtocolError("Tool Call did not reach a dispatchable state")
