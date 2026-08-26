"""WorkflowExecutor 隐藏 LangGraph 细节并只暴露统一协议。"""

from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.ai_chat.workflow import ConversationWorkflow
from app.workflow_runtime import WorkflowCatalog
from app.workflow_runtime.errors import (
    WorkflowNotRegisteredError,
    WorkflowRegistrationError,
)
from app.workflow_runtime.events import RuntimeEvent
from app.workflow_runtime.graph import (
    CheckpointedWorkflowExecutor,
    LangGraphExecutor,
)
from app.workflow_runtime.protocol import (
    GraphOutcome,
    GraphResumeCommand,
    InteractionRequest,
)
from app.workflow_runtime.tools import ToolApprovalPolicy


class _State(TypedDict):
    run_id: int
    resolved: bool


class _Runtime:
    def bind_tools(self, *_args):  # type: ignore[no-untyped-def]
        return self


class _TestWorkflow(ConversationWorkflow[_State]):
    def __init__(self) -> None:
        self.initialized_runs: list[int] = []

    async def validate_request(self, subject, scope):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def init_state(self, value):  # type: ignore[no-untyped-def]
        self.initialized_runs.append(value["run_id"])
        return _State(run_id=value["run_id"], resolved=False)

    def build_graph(self, _model, _tools):  # type: ignore[no-untyped-def]
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


def _input(run_id: int = 7):
    return {
        "conversation_id": 3,
        "run_id": run_id,
        "subject": {},
        "scope": {},
        "language": "zh",
        "run_kind": "user_turn",
        "tools_enabled": True,
        "messages": [],
        "pending_tool_results": [],
    }


async def test_graph_runner_returns_event_then_waiting_outcome() -> None:
    catalog = WorkflowCatalog()
    catalog.register(_TestWorkflow())
    runner = CheckpointedWorkflowExecutor(
        catalog.get,
        InMemorySaver(),
        object(),  # type: ignore[arg-type]
        _Runtime(),  # type: ignore[arg-type]
        thread_namespace="test",
    )

    items = [
        item
        async for item in runner.stream(
            workflow_name="_TestWorkflow", thread_id=7, value=_input()
        )
    ]

    assert isinstance(items[0], RuntimeEvent)
    assert items[0].type == "output.delta"
    assert isinstance(items[1], GraphOutcome)
    assert items[1].status == "waiting"
    assert items[1].interaction is not None
    assert items[1].interaction.interaction_id == 11
    recovery = await runner.recover(workflow_name="_TestWorkflow", thread_id=7)
    assert recovery.outcome is not None
    assert recovery.outcome.status == "waiting"


async def test_graph_runner_resume_accepts_only_minimal_command() -> None:
    catalog = WorkflowCatalog()
    catalog.register(_TestWorkflow())
    runner = CheckpointedWorkflowExecutor(
        catalog.get,
        InMemorySaver(),
        object(),  # type: ignore[arg-type]
        _Runtime(),  # type: ignore[arg-type]
        thread_namespace="test",
    )
    await _collect(
        runner.stream(workflow_name="_TestWorkflow", thread_id=7, value=_input())
    )

    items = await _collect(
        runner.resume(
            workflow_name="_TestWorkflow",
            thread_id=7,
            command=GraphResumeCommand(run_id=7, interaction_id=11),
        )
    )

    assert items == [GraphOutcome.completed()]


async def test_each_run_initializes_an_independent_workflow_thread() -> None:
    catalog = WorkflowCatalog()
    workflow = _TestWorkflow()
    catalog.register(workflow)
    runner = CheckpointedWorkflowExecutor(
        catalog.get,
        InMemorySaver(),
        object(),  # type: ignore[arg-type]
        _Runtime(),  # type: ignore[arg-type]
        thread_namespace="test",
    )
    first = await _collect(
        runner.stream(workflow_name="_TestWorkflow", thread_id=7, value=_input(7))
    )
    assert isinstance(first[-1], GraphOutcome)
    assert first[-1].status == "waiting"
    second = await _collect(
        runner.stream(
            workflow_name="_TestWorkflow",
            thread_id=8,
            value=_input(8),
        )
    )

    assert isinstance(second[0], RuntimeEvent)
    assert second[0].type == "output.delta"
    assert isinstance(second[-1], GraphOutcome)
    assert second[-1].status == "waiting"
    assert workflow.initialized_runs == [7, 8]
    assert (
        await runner.recover(workflow_name="_TestWorkflow", thread_id=7)
    ).outcome == first[-1]
    assert (
        await runner.recover(workflow_name="_TestWorkflow", thread_id=8)
    ).outcome == second[-1]


def test_workflow_catalog_rejects_duplicate_and_unknown_names() -> None:
    catalog = WorkflowCatalog()
    catalog.register(_TestWorkflow())

    with pytest.raises(WorkflowRegistrationError):
        catalog.register(_TestWorkflow())
    with pytest.raises(WorkflowNotRegisteredError):
        catalog.get("missing")


def test_graph_executor_rejects_legacy_dict_custom_events() -> None:
    with pytest.raises(ValueError, match="RuntimeEvent"):
        LangGraphExecutor.normalize(
            {
                "type": "custom",
                "data": {"type": "output.delta", "payload": {"text": "legacy"}},
            }
        )


async def _collect(stream):  # type: ignore[no-untyped-def]
    return [item async for item in stream]
