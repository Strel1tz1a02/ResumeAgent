"""GraphRunner 隐藏 LangGraph 细节并只暴露统一协议。"""

from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.ai_chat.adapters import AdapterRegistry, BaseAdapter
from app.ai_chat.graph import LangGraphDriver
from app.ai_chat.graph.runner import GraphRunner
from app.ai_chat.protocol import GraphOutcome, GraphResumeCommand, InteractionRequest
from app.ai_chat.streaming.events import RuntimeEvent
from app.ai_chat.tools.approval import ToolApprovalPolicy


class _State(TypedDict):
    run_id: int
    resolved: bool


class _Runtime:
    def bind_tools(self, *_args):  # type: ignore[no-untyped-def]
        return self


class _Adapter(BaseAdapter[_State]):
    async def validate_request(self, subject, scope):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def parse_input(self, value):  # type: ignore[no-untyped-def]
        return _State(run_id=value["run_id"], resolved=False)

    def build_graph(self, _runtime):  # type: ignore[no-untyped-def]
        def emit(state: _State):
            get_stream_writer()(RuntimeEvent("output.delta", {"text": "ready"}))
            return state

        def wait(state: _State):
            value = interrupt(
                InteractionRequest(
                    interaction_id=11,
                    kind="approval",
                    payload={"proposal": {"field": "background"}},
                ).interrupt_value()
            )
            command = GraphResumeCommand.from_value(value)
            assert command.run_id == state["run_id"]
            return {"resolved": True}

        graph = StateGraph(_State)
        graph.add_node("emit", emit)
        graph.add_node("wait", wait)
        graph.add_edge(START, "emit")
        graph.add_edge("emit", "wait")
        graph.add_edge("wait", END)
        return graph

    def get_tools(self):  # type: ignore[no-untyped-def]
        return {}

    def get_tool_approval_policy(self):  # type: ignore[no-untyped-def]
        return ToolApprovalPolicy()


def _input():
    return {
        "conversation_id": 3,
        "run_id": 7,
        "subject": {},
        "scope": {},
        "language": "zh",
        "run_kind": "user_turn",
        "tools_enabled": True,
        "messages": [],
        "pending_tool_results": [],
    }


async def test_graph_runner_returns_event_then_waiting_outcome() -> None:
    registry = AdapterRegistry()
    registry.register(_Adapter())
    runner = GraphRunner(registry, InMemorySaver(), _Runtime())  # type: ignore[arg-type]

    items = [item async for item in runner.stream(adapter_name="_Adapter", value=_input())]

    assert isinstance(items[0], RuntimeEvent)
    assert items[0].type == "output.delta"
    assert isinstance(items[1], GraphOutcome)
    assert items[1].status == "waiting"
    assert items[1].interaction is not None
    assert items[1].interaction.interaction_id == 11


async def test_graph_runner_resume_accepts_only_minimal_command() -> None:
    registry = AdapterRegistry()
    registry.register(_Adapter())
    runner = GraphRunner(registry, InMemorySaver(), _Runtime())  # type: ignore[arg-type]
    await _collect(runner.stream(adapter_name="_Adapter", value=_input()))

    items = await _collect(
        runner.resume(
            adapter_name="_Adapter",
            conversation_id=3,
            command=GraphResumeCommand(run_id=7, interaction_id=11),
        )
    )

    assert items == [GraphOutcome.completed()]


def test_graph_driver_rejects_legacy_dict_custom_events() -> None:
    with pytest.raises(ValueError, match="RuntimeEvent"):
        LangGraphDriver.normalize(
            {
                "type": "custom",
                "data": {"type": "output.delta", "payload": {"text": "legacy"}},
            }
        )


async def _collect(stream):  # type: ignore[no-untyped-def]
    return [item async for item in stream]
