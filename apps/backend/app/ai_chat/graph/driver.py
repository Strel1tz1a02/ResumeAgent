"""与业务拓扑无关的 LangGraph Driver。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol, TypeAlias

from langgraph.types import Command

from app.ai_chat.protocol import GraphOutcome, GraphResumeCommand, InteractionRequest
from app.ai_chat.streaming.events import RuntimeEvent

GraphStreamItem: TypeAlias = RuntimeEvent | GraphOutcome


@dataclass(frozen=True)
class GraphRecovery:
    """只读检查 checkpoint 后得到的稳定边界。"""

    outcome: GraphOutcome | None

    @property
    def requires_continue(self) -> bool:
        """失败/崩溃检查点没有 Outcome，需要在认领 Run 后继续。"""
        return self.outcome is None


class GraphDriver(Protocol):
    """任何 Graph 引擎接入 Runtime 时必须实现的最小接口。"""

    async def stream(
        self,
        *,
        graph: Any,
        graph_input: Any,
        config: dict[str, Any] | None = None,
    ) -> AsyncIterator[GraphStreamItem]: ...

    async def resume(
        self,
        *,
        graph: Any,
        command: GraphResumeCommand,
        config: dict[str, Any],
    ) -> AsyncIterator[GraphStreamItem]: ...

    async def recover(
        self,
        *,
        graph: Any,
        config: dict[str, Any],
    ) -> GraphRecovery: ...


class LangGraphDriver:
    """隐藏 LangGraph stream、Command、Interrupt 和 checkpoint 快照格式。"""

    async def stream(
        self,
        *,
        graph: Any,
        graph_input: Any,
        config: dict[str, Any] | None = None,
    ) -> AsyncIterator[GraphStreamItem]:
        """启动任意业务 Graph，并保证最后恰好返回一个 Outcome。"""
        async for part in graph.astream(
            graph_input,
            config=config,
            stream_mode=["updates", "custom"],
            version="v2",
        ):
            item = self.normalize(part)
            if item is None:
                continue
            yield item
            if isinstance(item, GraphOutcome):
                return
        yield GraphOutcome.completed()

    async def resume(
        self,
        *,
        graph: Any,
        command: GraphResumeCommand,
        config: dict[str, Any],
    ) -> AsyncIterator[GraphStreamItem]:
        """只用最小身份命令恢复一个已持久化的 Interaction。"""
        async for item in self.stream(
            graph=graph,
            graph_input=Command(resume=command.resume_value()),
            config=config,
        ):
            yield item

    async def recover(
        self,
        *,
        graph: Any,
        config: dict[str, Any],
    ) -> GraphRecovery:
        """只检查现有边界，不在 Run 被认领前执行任何 Graph 节点。"""
        snapshot = await graph.aget_state(config)
        interrupts = [
            item
            for task in snapshot.tasks
            for item in getattr(task, "interrupts", ())
        ]
        if interrupts:
            if len(interrupts) != 1:
                raise ValueError("Graph must expose exactly one active interaction")
            request = InteractionRequest.from_value(
                getattr(interrupts[0], "value", None)
            )
            return GraphRecovery(outcome=GraphOutcome.waiting(request))

        if getattr(snapshot, "next", ()):
            return GraphRecovery(outcome=None)
        return GraphRecovery(outcome=GraphOutcome.completed())

    @staticmethod
    def normalize(part: Any) -> GraphStreamItem | None:
        """将 LangGraph v2 输出转换成唯一 Runtime 协议。"""
        if isinstance(part, RuntimeEvent):
            return part
        if not isinstance(part, dict):
            return None
        event_type = part.get("type")
        data = part.get("data")
        if event_type == "custom":
            if isinstance(data, RuntimeEvent):
                return data
            raise ValueError("Graph custom output must be a RuntimeEvent")

        if event_type == "updates" and isinstance(data, dict):
            interrupts = data.get("__interrupt__")
            if interrupts:
                if not isinstance(interrupts, (tuple, list)) or len(interrupts) != 1:
                    raise ValueError("Graph must expose exactly one active interaction")
                return GraphOutcome.waiting(
                    InteractionRequest.from_value(
                        getattr(interrupts[0], "value", None)
                    )
                )
        return None
