"""提供给业务图节点的适配器级执行环境。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.ai_chat.streaming.model import AiChatModel, ModelStreamEvent
from app.ai_chat.tools.handler import ToolHandler
from app.ai_chat.types import JsonObject
from app.ai_chat.model_request import ModelRequestSpec
from app.ai_chat.model_request import build_model_request_spec

if TYPE_CHECKING:
    from app.ai_chat.services.tool_call_service import ToolCallService


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
        request_spec: JsonObject | None = None,
        tools_enabled: bool | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        """在执行本次工具策略的同时流式调用模型。"""
        spec = (
            ModelRequestSpec.model_validate(request_spec)
            if request_spec is not None
            else build_model_request_spec(
                self.tools.model_handlers,
                tools_enabled=bool(tools_enabled),
                requested_output=max_tokens,
            )
        )
        async for event in self.model.stream(
            messages=messages,
            request_spec=spec,
            handlers=self.tools.model_handlers,
            tools_enabled=bool(spec.tools),
            max_tokens=spec.max_tokens,
        ):
            yield event
