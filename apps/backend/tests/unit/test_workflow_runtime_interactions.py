"""通用 Interaction 协调器不依赖 Conversation 的契约测试。"""

from __future__ import annotations

from collections.abc import Collection

import pytest

from app.workflow_runtime.errors import IdempotencyConflictError
from app.workflow_runtime.events import RuntimeEvent
from app.workflow_runtime.graph import GraphRecovery
from app.workflow_runtime.interactions import (
    InteractionBinding,
    InteractionCoordinator,
)
from app.workflow_runtime.protocol import (
    GraphOutcome,
    GraphResumeCommand,
    InteractionRequest,
    InteractionResolution,
    ResolveInteractionCommand,
)
from app.workflow_runtime.tools import ToolApprovalPolicy, ToolCallIdentity


class _Workflow:
    def workflow_name(self) -> str:
        return "TestWorkflow"

    def get_tools(self):  # type: ignore[no-untyped-def]
        return {}

    def get_tool_approval_policy(self) -> ToolApprovalPolicy:
        return ToolApprovalPolicy()

    async def resolve_interaction(
        self,
        _tools,
        command: ResolveInteractionCommand,
    ) -> InteractionResolution:
        return InteractionResolution(
            resume=GraphResumeCommand(command.run_id, command.interaction_id),
            replayed=False,
        )


class _Tools:
    async def locate_call(self, tool_call_id: int) -> ToolCallIdentity:
        assert tool_call_id == 7
        return ToolCallIdentity(thread_id="task-1", run_id=5)

    def bind_tools(self, _tools, _policy):  # type: ignore[no-untyped-def]
        return self


class _Store:
    def __init__(self) -> None:
        self.status = "suspended"

    async def get_binding(self, identity: ToolCallIdentity) -> InteractionBinding:
        assert identity == ToolCallIdentity(thread_id="task-1", run_id=5)
        return InteractionBinding(
            workflow_name="TestWorkflow",
            checkpoint_id="run-5",
            run_id=5,
            run_status="suspended",
        )

    async def transition(
        self,
        run_id: int,
        *,
        from_statuses: Collection[str],
        to_status: str,
        error_code: str | None = None,
    ) -> bool:
        assert run_id == 5
        assert error_code is None
        if self.status not in from_statuses:
            return False
        self.status = to_status
        return True


class _Executor:
    async def recover(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs == {"workflow_name": "TestWorkflow", "thread_id": "run-5"}
        return GraphRecovery(
            GraphOutcome.waiting(InteractionRequest(7, "approval", {"proposal": {}}))
        )

    async def resume(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["workflow_name"] == "TestWorkflow"
        assert kwargs["thread_id"] == "run-5"
        assert kwargs["command"] == GraphResumeCommand(5, 7)
        yield RuntimeEvent("output.delta", {"text": "done"})
        yield GraphOutcome.completed()


def _command(*, run_id: int = 5) -> ResolveInteractionCommand:
    return ResolveInteractionCommand(
        run_id=run_id,
        interaction_id=7,
        kind="approval",
        client_resolution_id="resolution-1",
        payload={"decision": "approve"},
    )


async def test_interaction_coordinator_resumes_without_conversation() -> None:
    workflow = _Workflow()
    store = _Store()
    coordinator = InteractionCoordinator(
        lambda _name: workflow,  # type: ignore[arg-type]
        _Executor(),  # type: ignore[arg-type]
        _Tools(),  # type: ignore[arg-type]
        store,
    )

    events = [event async for event in coordinator.resolve(_command())]

    assert [event.type for event in events] == [
        "interaction.resolved",
        "output.delta",
        "run.completed",
    ]
    assert store.status == "completed"


async def test_interaction_identity_is_checked_before_workflow_resolution() -> None:
    coordinator = InteractionCoordinator(
        lambda _name: pytest.fail("workflow must not be resolved"),
        _Executor(),  # type: ignore[arg-type]
        _Tools(),  # type: ignore[arg-type]
        _Store(),
    )

    with pytest.raises(IdempotencyConflictError):
        _ = [event async for event in coordinator.resolve(_command(run_id=6))]
