"""Per-Adapter execution environment supplied to business Graph nodes."""

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
    """Stateless model and Tool dependencies for one concrete Adapter."""

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
        """Stream the model while enforcing this run's Tool policy."""
        async for event in self.model.stream(
            messages=messages,
            handlers=self.tool_handlers,
            tools_enabled=tools_enabled,
            max_tokens=max_tokens,
        ):
            yield event

    def handler(self, name: str) -> ToolHandler:
        """Return a declared Tool Handler or reject an undeclared Tool."""
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
        """Persist and dispatch an assembled Tool Call through its Handler."""
        return await self.tool_lifecycle.receive(
            conversation_id=conversation_id,
            run_id=run_id,
            subject=subject,
            target=target,
            call=call,
            handlers=dict(self.tool_handlers),
            adapter_context=adapter_context or {},
        )
