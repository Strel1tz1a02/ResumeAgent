"""ConversationService 的内部轮次编排。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from sqlalchemy.exc import IntegrityError

from app.ai_chat.errors import (
    ConversationEndedError,
    ConversationNotFoundError,
)
from app.ai_chat.persistence import SessionFactory
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.services.conversation_run_writer import ConversationRunWriter
from app.ai_chat.types import ConversationInput
from app.ai_chat.workflow import ConversationWorkflowResolver
from app.workflow_runtime.errors import (
    IdempotencyConflictError,
    InteractionStateError,
    RunInProgressError,
)
from app.workflow_runtime.events import (
    RuntimeEvent,
    outcome_events,
    run_event,
)
from app.workflow_runtime.graph import WorkflowExecutor
from app.workflow_runtime.protocol import GraphOutcome
from app.workflow_runtime.tools import ToolLifecycle
from app.workflow_runtime.types import JsonObject

logger = logging.getLogger(__name__)


async def _finish_cleanup(awaitable) -> None:  # type: ignore[no-untyped-def]
    """即使同一 Task 再次收到 cancel，也等待状态收敛完成。"""
    task = asyncio.create_task(awaitable)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    await task


class ConversationTurnCoordinator:
    """执行 Conversation 轮次；不是独立应用服务。"""

    def __init__(
        self,
        resolve_workflow: ConversationWorkflowResolver,
        workflows: WorkflowExecutor,
        repositories: RepositoryFactory,
        session_factory: SessionFactory,
        tools: ToolLifecycle,
    ) -> None:
        self._resolve_workflow = resolve_workflow
        self._workflows = workflows
        self._repositories = repositories
        self._session_factory = session_factory
        self._tools = tools
        self._runs = ConversationRunWriter(session_factory, repositories, tools)

    async def stream_opening(self, conversation_id: int) -> AsyncIterator[RuntimeEvent]:
        """启动并流式返回 Workflow 的开场 Run。"""
        async for event in self._stream_new_run(
            conversation_id=conversation_id,
            kind="opening",
            user_content=None,
            client_message_id=None,
        ):
            yield event

    async def stream_message(
        self,
        conversation_id: int,
        content: str,
        client_message_id: str,
    ) -> AsyncIterator[RuntimeEvent]:
        """幂等保存用户消息并启动一个 user_turn Run。"""
        async for event in self._stream_new_run(
            conversation_id=conversation_id,
            kind="user_turn",
            user_content=content,
            client_message_id=client_message_id,
        ):
            yield event

    async def _stream_new_run(
        self,
        *,
        conversation_id: int,
        kind: str,
        user_content: str | None,
        client_message_id: str | None,
    ) -> AsyncIterator[RuntimeEvent]:
        """原子创建消息与 Run，再把执行交给统一 Graph Driver。"""
        try:
            async with self._session_factory() as session:
                repositories = self._repositories.create(session)
                conversation = await repositories.conversations.get(conversation_id)
                if conversation is None:
                    raise ConversationNotFoundError(str(conversation_id))
                if conversation.status != "active":
                    raise ConversationEndedError(str(conversation_id))
                self._resolve_workflow(conversation.workflow_name)

                if client_message_id is not None:
                    existing = await repositories.messages.get_by_client_id(
                        conversation_id,
                        client_message_id,
                    )
                    if existing is not None:
                        if existing.content != user_content:
                            raise IdempotencyConflictError(client_message_id)
                        yield RuntimeEvent(
                            "command.replayed",
                            {"message_id": existing.id},
                            run_id=existing.run_id,
                        )
                        return

                if await repositories.runs.current(conversation_id) is not None:
                    raise RunInProgressError(str(conversation_id))
                run = await repositories.runs.create(
                    conversation_id=conversation_id,
                    kind=kind,
                    tools_enabled=True,
                )
                if user_content is not None:
                    await repositories.messages.create(
                        conversation_id=conversation_id,
                        run_id=run.id,
                        role="user",
                        content=user_content,
                        status="completed",
                        client_message_id=client_message_id,
                    )
                assistant = await repositories.messages.create(
                    conversation_id=conversation_id,
                    run_id=run.id,
                    role="assistant",
                    content="",
                    status="generating",
                )
                await session.commit()
                workflow_name = conversation.workflow_name
                run_id = run.id
                assistant_id = assistant.id
        except IntegrityError as exc:
            raise RunInProgressError(str(conversation_id)) from exc

        yield run_event(
            "run.started",
            run_id,
            {"output_id": assistant_id, "kind": kind},
        )
        try:
            value = await self._build_input(
                conversation_id=conversation_id,
                run_id=run_id,
                kind=kind,
                tools_enabled=True,
            )
        except Exception as exc:  # noqa: BLE001 - terminate the durable Run
            code = getattr(exc, "code", "input_assembly_failed")
            await _finish_cleanup(
                self._runs.terminate_with_message(
                    run_id=run_id,
                    assistant_id=assistant_id,
                    status="failed",
                    error_code=code,
                    content="",
                )
            )
            yield run_event("run.failed", run_id, {"code": code})
            return
        async for event in self._execute(
            workflow_name=workflow_name,
            value=value,
            assistant_id=assistant_id,
        ):
            yield event

    async def _build_input(
        self,
        *,
        conversation_id: int,
        run_id: int,
        kind: str,
        tools_enabled: bool,
    ) -> ConversationInput:
        """加载当前 Run 消息和仍待补传的 Tool Result。"""
        async with self._session_factory() as session:
            repositories = self._repositories.create(session)
            conversation = await repositories.conversations.get(conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(str(conversation_id))
            messages = await repositories.messages.list_completed_for_run(run_id)
            pending_rows = await self._tools.pending_results(conversation_id)
            pending: list[JsonObject] = [
                {
                    "tool_call_id": row["tool_call_id"],
                    "provider_tool_call_id": row["provider_id"],
                    "tool_name": row["name"],
                    "arguments": row["arguments"],
                    "result": dict(row["result"] or {}),
                }
                for row in pending_rows
            ]
            return {
                "conversation_id": conversation.id,
                "run_id": run_id,
                "subject": conversation.subject,
                "scope": conversation.scope,
                "language": conversation.language,
                "run_kind": kind,
                "tools_enabled": tools_enabled,
                "messages": [
                    {"role": row.role, "content": row.content} for row in messages
                ],
                "pending_tool_results": pending,
            }

    async def _execute(
        self,
        *,
        workflow_name: str,
        value: ConversationInput,
        assistant_id: int,
    ) -> AsyncIterator[RuntimeEvent]:
        """消费统一 Graph 流，并提交唯一终止 Outcome。"""
        text = ""
        outcome: GraphOutcome | None = None
        try:
            async for item in self._workflows.stream(
                workflow_name=workflow_name,
                thread_id=value["run_id"],
                value=value,
            ):
                if isinstance(item, GraphOutcome):
                    outcome = item
                    break
                event = item.bind(run_id=value["run_id"])
                if event.type == "output.delta":
                    delta = event.payload.get("text")
                    if isinstance(delta, str):
                        text += delta
                yield event

            if outcome is None:
                raise InteractionStateError("Graph stream ended without an outcome")
            await self._runs.settle_graph(
                run_id=value["run_id"],
                outcome=outcome,
                assistant_id=assistant_id,
                content=text,
                delivered_tool_call_ids={
                    int(item["tool_call_id"]) for item in value["pending_tool_results"]
                },
            )
            for event in outcome_events(
                run_id=value["run_id"],
                outcome=outcome,
                completed_payload={"output_id": assistant_id, "content": text},
            ):
                yield event
        except asyncio.CancelledError:
            await _finish_cleanup(
                self._runs.terminate_with_message(
                    run_id=value["run_id"],
                    assistant_id=assistant_id,
                    status="cancelled",
                    error_code=None,
                    content=text,
                )
            )
            raise
        except Exception as exc:
            logger.exception(
                "Conversation run failed: conversation=%s run=%s",
                value["conversation_id"],
                value["run_id"],
            )
            code = getattr(exc, "code", "graph_execution_failed")
            await self._runs.terminate_with_message(
                run_id=value["run_id"],
                assistant_id=assistant_id,
                status="failed",
                error_code=code,
                content=text,
            )
            yield run_event("run.failed", value["run_id"], {"code": code})
