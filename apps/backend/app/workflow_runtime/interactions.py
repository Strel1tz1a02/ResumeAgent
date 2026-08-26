"""Interaction 的固化、恢复与 Run 收敛能力。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Collection
from dataclasses import dataclass
from typing import Protocol

from app.workflow_runtime.errors import (
    IdempotencyConflictError,
    InteractionStateError,
    RunInProgressError,
)
from app.workflow_runtime.events import (
    RuntimeEvent,
    interaction_resolved_event,
    outcome_events,
    run_event,
    tool_result_event,
)
from app.workflow_runtime.graph import WorkflowExecutor, WorkflowResolver
from app.workflow_runtime.protocol import (
    GraphOutcome,
    ResolveInteractionCommand,
    RunStatus,
)
from app.workflow_runtime.runs import RunStateMachine, RunStore
from app.workflow_runtime.tools import ToolCallIdentity, ToolLifecycle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InteractionBinding:
    """一个待处理 Interaction 所属的持久化 Workflow Run。"""

    workflow_name: str
    checkpoint_id: int | str
    run_id: int
    run_status: RunStatus


class InteractionStore(RunStore[int], Protocol):
    """由宿主提供的 Interaction 绑定查询与 Run 状态存储端口。"""

    async def get_binding(
        self,
        identity: ToolCallIdentity,
    ) -> InteractionBinding: ...


async def _finish_cleanup(awaitable) -> None:  # type: ignore[no-untyped-def]
    """即使调用方再次取消，也等待持久化状态完成收敛。"""
    task = asyncio.create_task(awaitable)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    await task


class InteractionCoordinator:
    """通用地固化 Interaction，并恢复其 Workflow 与 Run。"""

    def __init__(
        self,
        resolve_workflow: WorkflowResolver,
        workflows: WorkflowExecutor,
        tools: ToolLifecycle,
        store: InteractionStore,
    ) -> None:
        self._resolve_workflow = resolve_workflow
        self._workflows = workflows
        self._tools = tools
        self._store = store

    def _tool_calls(self, workflow_name: str) -> ToolLifecycle:
        workflow = self._resolve_workflow(workflow_name)
        return self._tools.bind_tools(
            workflow.get_tools(),
            workflow.get_tool_approval_policy(),
        )

    async def _transition(
        self,
        run_id: int,
        *,
        from_statuses: Collection[RunStatus],
        to_status: RunStatus,
        error_code: str | None = None,
        require: bool = True,
    ) -> bool:
        transitioned = await RunStateMachine(self._store).transition(
            run_id,
            from_statuses=from_statuses,
            to_status=to_status,
            error_code=error_code,
        )
        if transitioned or not require:
            return transitioned
        if to_status == "running":
            raise RunInProgressError(str(run_id))
        raise InteractionStateError(f"Run {run_id} cannot transition to {to_status}")

    async def resolve(
        self,
        command: ResolveInteractionCommand,
    ) -> AsyncIterator[RuntimeEvent]:
        """固化任意领域 Interaction，再恢复所属 Graph 和 Run。"""
        identity = await self._tools.locate_call(command.interaction_id)
        if identity.run_id != command.run_id:
            raise IdempotencyConflictError(command.client_resolution_id)
        binding = await self._store.get_binding(identity)
        if binding.run_id != identity.run_id:
            raise InteractionStateError(str(command.interaction_id))

        workflow = self._resolve_workflow(binding.workflow_name)
        tools = self._tool_calls(binding.workflow_name)
        try:
            resolution = await workflow.resolve_interaction(tools, command)
        except Exception:
            # 提交成功但连接关闭失败属于未知提交结果；同一幂等命令重放一次。
            logger.warning(
                "Interaction resolution had an unknown commit result; replaying: %s",
                command.interaction_id,
                exc_info=True,
            )
            resolution = await workflow.resolve_interaction(tools, command)

        if binding.run_status == "completed" and resolution.replayed:
            yield run_event(
                "command.replayed",
                command.run_id,
                {"interaction_id": command.interaction_id},
            )
            return

        recovery = await self._workflows.recover(
            workflow_name=binding.workflow_name,
            thread_id=binding.checkpoint_id,
        )
        if recovery.outcome is not None and recovery.outcome.status == "completed":
            await self._transition(
                command.run_id,
                from_statuses={"running", "suspended", "failed", "cancelled"},
                to_status="completed",
                require=False,
            )
            yield self._resolved_event(command)
            durable = await tools.get_call(command.interaction_id)
            if durable["status"] == "resolved" and durable["result"] is not None:
                yield tool_result_event(
                    tool_name=durable["name"],
                    tool_call_id=command.interaction_id,
                    result=durable["result"],
                ).bind(run_id=command.run_id)
            yield run_event("run.completed", command.run_id)
            return

        waiting = recovery.outcome.interaction if recovery.outcome is not None else None
        if recovery.outcome is not None and (
            waiting is None
            or waiting.interaction_id != command.interaction_id
            or waiting.kind != command.kind
        ):
            raise IdempotencyConflictError(command.client_resolution_id)

        await self._transition(
            command.run_id,
            from_statuses={"suspended", "failed", "cancelled"},
            to_status="running",
        )
        outcome: GraphOutcome | None = None
        try:
            yield self._resolved_event(command)
            stream = (
                self._workflows.continue_run(
                    workflow_name=binding.workflow_name,
                    thread_id=binding.checkpoint_id,
                )
                if recovery.requires_continue
                else self._workflows.resume(
                    workflow_name=binding.workflow_name,
                    thread_id=binding.checkpoint_id,
                    command=resolution.resume,
                )
            )
            async for item in stream:
                if isinstance(item, GraphOutcome):
                    outcome = item
                    break
                yield item.bind(run_id=command.run_id)
            if outcome is None:
                raise InteractionStateError("Resumed Graph ended without an outcome")
            await self._transition(
                command.run_id,
                from_statuses={"running"},
                to_status=("suspended" if outcome.status == "waiting" else "completed"),
            )
            for event in outcome_events(run_id=command.run_id, outcome=outcome):
                yield event
        except asyncio.CancelledError:
            await _finish_cleanup(
                self._transition(
                    command.run_id,
                    from_statuses={"running"},
                    to_status="cancelled",
                    require=False,
                )
            )
            raise
        except Exception:
            logger.exception(
                "Interaction resume failed: run=%s interaction=%s",
                command.run_id,
                command.interaction_id,
            )
            await self._transition(
                command.run_id,
                from_statuses={"running"},
                to_status="failed",
                error_code="interaction_finalize_failed",
                require=False,
            )
            yield run_event(
                "run.failed",
                command.run_id,
                {"code": "interaction_finalize_failed"},
            )

    @staticmethod
    def _resolved_event(command: ResolveInteractionCommand) -> RuntimeEvent:
        outcome = command.payload.get("decision")
        return interaction_resolved_event(
            interaction_id=command.interaction_id,
            kind=command.kind,
            outcome=outcome if isinstance(outcome, str) else "submitted",
        ).bind(run_id=command.run_id)
