"""提供给业务图节点的适配器级执行环境。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessageChunk

from app.ai_chat.tools.approval import ToolApprovalPolicy
from app.ai_chat.streaming.model import AiChatModel
from app.ai_chat.tools.operation import RegisteredTool
from app.ai_chat.types import JsonObject

if TYPE_CHECKING:
    from app.ai_chat.memory import MemoryService
    from app.ai_chat.services.tool_service import ToolService


@dataclass(frozen=True)
class AiChatRuntime:
    """一个具体适配器所需的无状态模型与统一 Tool 服务。"""

    model: AiChatModel
    tools: ToolService
    memory: MemoryService

    def bind_tools(
        self,
        tools: Mapping[str, RegisteredTool],
        approval: ToolApprovalPolicy | None = None,
    ) -> AiChatRuntime:
        """返回绑定指定工具集合的运行环境。"""
        return AiChatRuntime(
            model=self.model,
            tools=self.tools.bind_tools(
                tools,
                approval or self.tools.approval_policy,
            ),
            memory=self.memory,
        )

    async def stream_model(
        self,
        *,
        messages: list[JsonObject],
        tools_enabled: bool,
        max_tokens: int = 4096,
    ) -> AsyncIterator[AIMessageChunk]:
        """在执行本次工具策略的同时流式调用模型。"""
        tools = self.tools.model_tools
        async for chunk in self.model.stream(
            messages=messages,
            tools=tools,
            tools_enabled=tools_enabled,
            max_tokens=max_tokens,
        ):
            yield chunk
