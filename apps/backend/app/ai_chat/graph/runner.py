"""Compile and execute business-defined LangGraphs."""

import json
from collections.abc import AsyncIterator
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app.ai_chat.adapters.base import BaseAdapter
from app.ai_chat.model import AiChatModel
from app.ai_chat.registry import AdapterRegistry
from app.ai_chat.runtime import AiChatRuntime
from app.ai_chat.streaming.events import AiChatEvent
from app.ai_chat.tools.lifecycle import ToolLifecycle
from app.ai_chat.types import AdapterInput, ApprovalInput, JsonObject


class GraphRunner:
    """Cache compiled business Graphs and normalize their v2 stream output."""

    def __init__(
        self,
        registry: AdapterRegistry,
        checkpointer: AsyncSqliteSaver,
        model: AiChatModel,
        tool_lifecycle: ToolLifecycle,
    ) -> None:
        """Keep shared dependencies and an Adapter-name Graph cache."""
        self._registry = registry
        self._checkpointer = checkpointer
        self._model = model
        self._tool_lifecycle = tool_lifecycle
        self._graphs: dict[str, Any] = {}

    def _compiled(self, adapter: BaseAdapter) -> Any:
        """Compile one Adapter Graph once for this process."""
        name = adapter.adapter_name()
        if name not in self._graphs:
            runtime = AiChatRuntime(
                self._model,
                adapter.get_tool_handlers(),
                self._tool_lifecycle,
            )
            self._graphs[name] = adapter.build_graph(runtime).compile(
                checkpointer=self._checkpointer
            )
        return self._graphs[name]

    async def stream(
        self,
        *,
        adapter_name: str,
        value: AdapterInput,
        resume: JsonObject | ApprovalInput | None = None,
    ) -> AsyncIterator[AiChatEvent]:
        """Run or resume a Graph with a stable conversation thread ID."""
        adapter = self._registry.get(adapter_name)
        graph = self._compiled(adapter)
        if resume is None:
            business_state = await adapter.parse_input(value)
            graph_input: Any = {**value, **business_state}
            json.dumps(graph_input, ensure_ascii=False)
        else:
            graph_input = Command(resume=resume)
        config = {
            "configurable": {
                "thread_id": f"ai-chat:{value['conversation_id']}",
            }
        }
        if resume is not None:
            await graph.aupdate_state(
                config,
                {
                    "run_id": value["run_id"],
                    "run_kind": value["run_kind"],
                    "tools_enabled": value["tools_enabled"],
                    "approval": value.get("approval"),
                    "messages": value["messages"],
                    "pending_tool_results": value["pending_tool_results"],
                },
            )
        async for part in graph.astream(
            graph_input,
            config=config,
            stream_mode=["messages", "updates", "custom"],
            version="v2",
        ):
            event = self._normalize(part)
            if event is not None:
                yield event

    @staticmethod
    def _normalize(part: Any) -> AiChatEvent | None:
        """Normalize v2 messages/custom/interrupt chunks into internal events."""
        if isinstance(part, AiChatEvent):
            return part
        if not isinstance(part, dict):
            return None
        event_type = part.get("type")
        data = part.get("data")
        if event_type == "custom":
            if isinstance(data, AiChatEvent):
                return data
            if isinstance(data, dict) and isinstance(data.get("event"), str):
                payload = data.get("data")
                return AiChatEvent(
                    data["event"], payload if isinstance(payload, dict) else {}
                )
        if event_type == "messages":
            message = data[0] if isinstance(data, tuple) and data else data
            content = getattr(message, "content", "")
            if isinstance(content, str) and content:
                return AiChatEvent("assistant.delta", {"text": content})
        if event_type == "updates" and isinstance(data, dict):
            interrupts = data.get("__interrupt__")
            if interrupts:
                interrupt = interrupts[0]
                payload = getattr(interrupt, "value", interrupt)
                if isinstance(payload, dict):
                    return AiChatEvent("_graph.interrupted", {"payload": payload})
        return None

    async def delete_thread(self, conversation_id: int) -> None:
        """Delete all checkpoints for one conversation."""
        await self._checkpointer.adelete_thread(f"ai-chat:{conversation_id}")
