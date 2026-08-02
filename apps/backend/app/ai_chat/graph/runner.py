"""编译并执行业务定义的 LangGraph。"""

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
    """缓存已编译业务图，并规范化其 v2 流输出。"""

    def __init__(
        self,
        registry: AdapterRegistry,
        checkpointer: AsyncSqliteSaver,
        model: AiChatModel,
        tool_lifecycle: ToolLifecycle,
    ) -> None:
        """保存共享依赖和按适配器名称索引的业务图缓存。"""
        self._registry = registry
        self._checkpointer = checkpointer
        self._model = model
        self._tool_lifecycle = tool_lifecycle
        self._graphs: dict[str, Any] = {}

    def _compiled(self, adapter: BaseAdapter) -> Any:
        """在当前进程中仅编译一次适配器业务图。"""
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
        """使用稳定的会话线程 ID 运行或恢复业务图。"""
        adapter = self._registry.get(adapter_name)
        graph = self._compiled(adapter)
        if resume is None:
            business_state = await adapter.parse_input(value)
            graph_input: Any = {
                **value,
                **business_state.model_dump(mode="json"),
            }
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
        """将 v2 消息、自定义事件和中断片段规范化为内部事件。"""
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
        """删除一个会话的全部检查点。"""
        await self._checkpointer.adelete_thread(f"ai-chat:{conversation_id}")
