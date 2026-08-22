"""将 Adapter Graph 接到与业务拓扑无关的统一 Driver。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.ai_chat.adapters import AdapterRegistry, BaseAdapter
from app.ai_chat.graph.driver import (
    GraphDriver,
    GraphRecovery,
    GraphStreamItem,
    LangGraphDriver,
)
from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.protocol import GraphResumeCommand
from app.ai_chat.types import AdapterInput

__all__ = ["GraphRunner"]


class GraphRunner:
    """负责 Adapter 解析/编译；Graph 执行语义全部委托给 Driver。"""

    def __init__(
        self,
        registry: AdapterRegistry,
        checkpointer: AsyncSqliteSaver,
        runtime: AiChatRuntime,
        driver: GraphDriver | None = None,
    ) -> None:
        self._registry = registry
        self._checkpointer = checkpointer
        self._runtime = runtime
        self._driver = driver or LangGraphDriver()
        self._graphs: dict[str, Any] = {}

    def _compiled(self, adapter: BaseAdapter) -> Any:
        """按 Adapter 缓存业务自有的 Graph 拓扑。"""
        name = adapter.adapter_name()
        if name not in self._graphs:
            runtime = self._runtime.bind_tools(
                adapter.get_tools(),
                adapter.get_tool_approval_policy(),
            )
            self._graphs[name] = adapter.build_graph(runtime).compile(
                checkpointer=self._checkpointer
            )
        return self._graphs[name]

    @staticmethod
    def _config(conversation_id: int) -> dict[str, Any]:
        return {"configurable": {"thread_id": f"ai-chat:{conversation_id}"}}

    async def stream(
        self,
        *,
        adapter_name: str,
        value: AdapterInput,
    ) -> AsyncIterator[GraphStreamItem]:
        adapter = self._registry.get(adapter_name)
        graph_input: Any = await adapter.parse_input(value)
        json.dumps(graph_input, ensure_ascii=False)
        async for item in self._driver.stream(
            graph=self._compiled(adapter),
            graph_input=graph_input,
            config=self._config(value["conversation_id"]),
        ):
            yield item

    async def resume(
        self,
        *,
        adapter_name: str,
        conversation_id: int,
        command: GraphResumeCommand,
    ) -> AsyncIterator[GraphStreamItem]:
        adapter = self._registry.get(adapter_name)
        async for item in self._driver.resume(
            graph=self._compiled(adapter),
            command=command,
            config=self._config(conversation_id),
        ):
            yield item

    async def continue_run(
        self,
        *,
        adapter_name: str,
        conversation_id: int,
    ) -> AsyncIterator[GraphStreamItem]:
        """在 Runtime 已重新认领 Run 后继续失败或崩溃的 checkpoint。"""
        adapter = self._registry.get(adapter_name)
        async for item in self._driver.stream(
            graph=self._compiled(adapter),
            graph_input=None,
            config=self._config(conversation_id),
        ):
            yield item

    async def delete_thread(self, conversation_id: int) -> None:
        await self._checkpointer.adelete_thread(f"ai-chat:{conversation_id}")

    async def recover(
        self,
        *,
        adapter_name: str,
        conversation_id: int,
    ) -> GraphRecovery:
        """只读识别当前 checkpoint 边界，不执行任何业务节点。"""
        adapter = self._registry.get(adapter_name)
        return await self._driver.recover(
            graph=self._compiled(adapter),
            config=self._config(conversation_id),
        )
