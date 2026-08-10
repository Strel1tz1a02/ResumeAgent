"""提供给业务图节点的适配器级执行环境。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.ai_chat.streaming.model import (
    AiChatModel,
    ModelStreamEvent,
    build_model_tools,
)
from app.ai_chat.tools.handler import ToolHandler
from app.ai_chat.types import JsonObject

if TYPE_CHECKING:
    from app.ai_chat.memory import MemoryService
    from app.ai_chat.services.tool_call_service import ToolCallService


@dataclass(frozen=True)
class AiChatRuntime:
    """一个具体适配器所需的无状态模型与统一 Tool 服务。"""

    model: AiChatModel
    tools: ToolCallService
    memory: MemoryService

    def bind_tools(
        self,
        tool_handlers: Mapping[str, ToolHandler],
    ) -> AiChatRuntime:
        """返回绑定指定工具集合的运行环境。"""
        return AiChatRuntime(
            model=self.model,
            tools=self.tools.bind_handlers(tool_handlers),
            memory=self.memory,
        )

    async def stream_model(
        self,
        *,
        run_id: int,
        messages: list[JsonObject],
        tools_enabled: bool,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ModelStreamEvent]:
        """在执行本次工具策略的同时流式调用模型。"""
        handlers = self.tools.model_handlers
        tool_definitions = (
            build_model_tools(handlers)
            if tools_enabled and handlers
            else None
        )
        request_messages = await self.memory.prepare_request_messages(
            run_id,
            messages,
            tools=tool_definitions,
        )
        async for event in self.model.stream(
            messages=request_messages,
            handlers=handlers,
            tools_enabled=tools_enabled,
            max_tokens=max_tokens,
        ):
            yield event
