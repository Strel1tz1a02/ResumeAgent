"""与业务拓扑无关的 LangGraph Driver。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from langgraph.types import Command

from app.workflow_runtime.events import RuntimeEvent
from app.workflow_runtime.protocol import (
    GraphOutcome,
    GraphResumeCommand,
    InteractionRequest,
)

type GraphStreamItem = RuntimeEvent | GraphOutcome


@dataclass(frozen=True)
class GraphRecovery:
    outcome: GraphOutcome | None

    @property
    def requires_continue(self) -> bool:
        return self.outcome is None


class GraphExecutor(Protocol):
    """执行一个已编译 Graph；不负责 Workflow 解析或 checkpoint 命名。"""

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


class LangGraphExecutor:
    """LangGraph 的最小执行适配器。"""

    async def stream(
        self,
        *,
        graph: Any,
        graph_input: Any,
        config: dict[str, Any] | None = None,
    ) -> AsyncIterator[GraphStreamItem]:
        async for part in graph.astream(
            graph_input,
            config=config,
            stream_mode=["updates", "custom"],
            subgraphs=True,
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
        snapshot = await graph.aget_state(config, subgraphs=True)
        interrupts = self._snapshot_interrupts(snapshot)
        if interrupts:
            if len(interrupts) != 1:
                raise ValueError("Graph must expose exactly one active interrupt")
            return GraphRecovery(
                outcome=self._interrupt_outcome(
                    getattr(interrupts[0], "value", None)
                )
            )

        if getattr(snapshot, "next", ()):
            return GraphRecovery(outcome=None)
        return GraphRecovery(outcome=GraphOutcome.completed())

    @staticmethod
    def _snapshot_interrupts(snapshot: Any) -> list[Any]:
        """递归读取父图及其子图快照中的唯一活动 interrupt。"""
        found: dict[str, Any] = {}

        def visit(current: Any) -> None:
            for item in getattr(current, "interrupts", ()):
                identity = str(getattr(item, "id", id(item)))
                found[identity] = item
            for task in getattr(current, "tasks", ()):
                for item in getattr(task, "interrupts", ()):
                    identity = str(getattr(item, "id", id(item)))
                    found[identity] = item
                nested = getattr(task, "state", None)
                if nested is not None and hasattr(nested, "tasks"):
                    visit(nested)

        visit(snapshot)
        return list(found.values())

    @staticmethod
    def normalize(part: Any) -> GraphStreamItem | None:
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
                    raise ValueError("Graph must expose exactly one active interrupt")
                return LangGraphExecutor._interrupt_outcome(
                    getattr(interrupts[0], "value", None)
                )
        return None

    @staticmethod
    def _interrupt_outcome(value: object) -> GraphOutcome:
        return GraphOutcome.waiting(InteractionRequest.from_value(value))
