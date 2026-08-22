"""提供给业务图节点的适配器级执行环境。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessageChunk
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.ai_chat.context import ContextAssembler, ModelContext
from app.ai_chat.streaming.model import AiChatModel
from app.ai_chat.tools.approval import ToolApprovalPolicy
from app.ai_chat.tools.operation import RegisteredTool
from app.llm import DEFAULT_JSON_MAX_TOKENS

if TYPE_CHECKING:
    from app.ai_chat.services.tool_service import ToolService


@dataclass(frozen=True)
class AiChatRuntime:
    """一个具体适配器所需的无状态模型与统一 Tool 服务。"""

    model: AiChatModel
    tools: ToolService
    context: ContextAssembler
    max_tokens: int = DEFAULT_JSON_MAX_TOKENS

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
            context=self.context,
            max_tokens=self.max_tokens,
        )

    async def stream_model(
        self,
        *,
        run_id: int,
        context: ModelContext,
        tools_enabled: bool,
        max_tokens: int | None = None,
    ) -> AsyncIterator[AIMessageChunk]:
        """先经过唯一 ContextAssembler，再按本次工具策略调用模型。"""
        tools = self.tools.model_tools
        tool_schemas = (
            [convert_to_openai_tool(tool) for tool in tools.values()]
            if tools_enabled
            else None
        )
        messages = await self.context.assemble(
            run_id=run_id,
            context=context,
            tools=tool_schemas,
        )
        async for chunk in self.model.stream(
            messages=messages,
            tools=tools,
            tools_enabled=tools_enabled,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
        ):
            yield chunk
