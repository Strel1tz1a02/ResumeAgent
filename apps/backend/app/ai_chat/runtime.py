"""提供给业务图节点的适配器级执行环境。"""

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass

from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.model import AiChatModel, ModelStreamEvent
from app.ai_chat.tools.buffer import AssembledToolCall
from app.ai_chat.tools.handler import ToolHandler
from app.ai_chat.tools.lifecycle import ToolDispatch, ToolLifecycle
from app.ai_chat.types import JsonObject


@dataclass(frozen=True)
class AiChatRuntime:
    """一个具体适配器所需的无状态模型与工具依赖。"""

    model: AiChatModel
    tool_handlers: Mapping[str, ToolHandler]
    tool_lifecycle: ToolLifecycle

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
            handlers=self.tool_handlers,
            tools_enabled=tools_enabled,
            max_tokens=max_tokens,
        ):
            yield event

    def handler(self, name: str) -> ToolHandler:
        """返回已声明的工具处理器，拒绝未声明的工具。"""
        try:
            return self.tool_handlers[name]
        except KeyError as error:
            raise ToolProtocolError(f"Tool is not declared by this Adapter: {name}") from error

    async def receive_tool_call(
        self,
        *,
        conversation_id: int,
        run_id: int,
        subject: JsonObject,
        target: JsonObject,
        call: AssembledToolCall,
        adapter_context: JsonObject | None = None,
    ) -> ToolDispatch:
        """通过对应处理器持久化并分派已组装的工具调用。"""
        return await self.tool_lifecycle.receive(
            conversation_id=conversation_id,
            run_id=run_id,
            subject=subject,
            target=target,
            call=call,
            handlers=dict(self.tool_handlers),
            adapter_context=adapter_context or {},
        )
