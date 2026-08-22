"""统一 Agent Runtime 的会话、Run、Graph 和 Interaction 应用服务。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from sqlalchemy.exc import IntegrityError

from app import database as database_module
from app.ai_chat.adapters import AdapterRegistry
from app.ai_chat.errors import (
    ConversationEndedError,
    ConversationNotFoundError,
    IdempotencyConflictError,
    InteractionStateError,
    RunInProgressError,
    ToolCallNotFoundError,
)
from app.ai_chat.graph.runner import GraphRunner
from app.ai_chat.protocol import GraphOutcome, ResolveInteractionCommand
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.services.run_lifecycle import RunLifecycleService
from app.ai_chat.services.tool_service import ToolService
from app.ai_chat.streaming.events import (
    interaction_requested_event,
    interaction_resolved_event,
    run_event,
    RuntimeEvent,
    tool_result_event,
)
from app.ai_chat.tools.store import ToolCallStore
from app.ai_chat.types import AdapterInput, JsonObject, ScopeRef, SubjectRef

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


class AiChatService:
    """作为唯一控制面协调通用 Run 生命周期和业务 Graph。"""

    def __init__(
        self,
        registry: AdapterRegistry,
        runner: GraphRunner,
        repositories: RepositoryFactory,
    ) -> None:
        """保存无状态协作者；请求事务均由方法内部创建。"""
        self._registry = registry
        self._runner = runner
        self._repositories = repositories

    @property
    def _runs(self) -> RunLifecycleService:
        """返回绑定当前数据库工厂的统一 Run 生命周期服务。"""
        return RunLifecycleService(database_module.db.session, self._repositories)

    def _tool_calls(self, adapter_name: str) -> ToolService:
        """返回绑定指定 Adapter 工具和策略的 Tool Runtime。"""
        adapter = self._registry.get(adapter_name)
        return ToolService(
            ToolCallStore(database_module.db.session, self._repositories)
        ).bind_tools(adapter.get_tools(), adapter.get_tool_approval_policy())

    async def create_conversation(
        self,
        adapter_name: str,
        subject: JsonObject,
        scope: JsonObject,
        language: str = "zh",
    ) -> int:
        """由 Adapter 校验业务绑定后持久化会话。"""
        adapter = self._registry.get(adapter_name)
        binding = await adapter.validate_request(
            SubjectRef.model_validate(subject),
            ScopeRef.model_validate(scope),
        )
        async with database_module.db.session() as session:
            row = await self._repositories.create(session).conversations.create(
                adapter=adapter_name,
                subject=binding.subject.model_dump(mode="json"),
                scope=binding.scope.model_dump(mode="json"),
                language=language or "zh",
            )
            await session.commit()
            return row.id

    async def stream_opening(self, conversation_id: int) -> AsyncIterator[RuntimeEvent]:
        """启动并流式返回 Adapter 的开场 Run。"""
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
            async with database_module.db.session() as session:
                repositories = self._repositories.create(session)
                conversation = await repositories.conversations.get(conversation_id)
                if conversation is None:
                    raise ConversationNotFoundError(str(conversation_id))
                if conversation.status != "active":
                    raise ConversationEndedError(str(conversation_id))
                self._registry.get(conversation.adapter)

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
                adapter_name = conversation.adapter
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
        except Exception as exc:
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
            adapter_name=adapter_name,
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
    ) -> AdapterInput:
        """加载当前 Run 消息和仍待补传的 Tool Result。"""
        async with database_module.db.session() as session:
            repositories = self._repositories.create(session)
            conversation = await repositories.conversations.get(conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(str(conversation_id))
            messages = await repositories.messages.list_completed_for_run(run_id)
            pending_rows = await repositories.tool_calls.pending_results(conversation_id)
            pending: list[JsonObject] = [
                {
                    "tool_call_id": row.id,
                    "provider_tool_call_id": row.provider_tool_call_id,
                    "tool_name": row.tool_name,
                    "arguments": row.arguments,
                    "result": dict(row.tool_result or {}),
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
        adapter_name: str,
        value: AdapterInput,
        assistant_id: int,
    ) -> AsyncIterator[RuntimeEvent]:
        """消费统一 Graph 流，并提交唯一终止 Outcome。"""
        text = ""
        outcome: GraphOutcome | None = None
        try:
            async for item in self._runner.stream(
                adapter_name=adapter_name,
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
                    int(item["tool_call_id"])
                    for item in value["pending_tool_results"]
                },
            )
            async for event in self._outcome_events(
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
                "Agent Runtime run failed: conversation=%s run=%s",
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

    async def resolve_interaction(
        self,
        command: ResolveInteractionCommand,
    ) -> AsyncIterator[RuntimeEvent]:
        """固化任意领域 Interaction，再恢复所属 Graph 和 Run。"""
        async with database_module.db.session() as session:
            repositories = self._repositories.create(session)
            row = await repositories.tool_calls.get(command.interaction_id)
            if row is None:
                raise ToolCallNotFoundError(str(command.interaction_id))
            if row.run_id != command.run_id:
                raise IdempotencyConflictError(command.client_resolution_id)
            conversation = await repositories.conversations.get(row.conversation_id)
            run = await repositories.runs.get(row.run_id)
            if conversation is None or run is None:
                raise InteractionStateError(str(command.interaction_id))
            if conversation.status != "active":
                raise ConversationEndedError(str(conversation.id))
            adapter = self._registry.get(conversation.adapter)
            adapter_name = conversation.adapter
            conversation_id = conversation.id
            run_status = run.status

        try:
            resolution = await adapter.resolve_interaction(
                self._tool_calls(adapter_name),
                command,
            )
        except Exception:
            # 提交成功但连接关闭失败属于未知提交结果；同一幂等命令重放一次
            # 即可收敛，不让 transport 异常覆盖已持久化的 Resolution。
            logger.warning(
                "Interaction resolution had an unknown commit result; replaying: %s",
                command.interaction_id,
                exc_info=True,
            )
            resolution = await adapter.resolve_interaction(
                self._tool_calls(adapter_name),
                command,
            )
        if run_status == "completed" and resolution.replayed:
            yield run_event(
                "command.replayed",
                command.run_id,
                {"interaction_id": command.interaction_id},
            )
            return

        recovery = await self._runner.recover(
            adapter_name=adapter_name,
            conversation_id=conversation_id,
        )
        if recovery.outcome is not None and recovery.outcome.status == "completed":
            await self._runs.transition(
                command.run_id,
                from_statuses={"running", "suspended", "failed", "cancelled"},
                to_status="completed",
                require=False,
            )
            yield self._resolved_event(command)
            durable = await self._tool_calls(adapter_name).get_call(
                command.interaction_id
            )
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

        await self._runs.transition(
            command.run_id,
            from_statuses={"suspended", "failed", "cancelled"},
            to_status="running",
        )
        outcome: GraphOutcome | None = None
        try:
            yield self._resolved_event(command)
            stream = (
                self._runner.continue_run(
                    adapter_name=adapter_name,
                    conversation_id=conversation_id,
                )
                if recovery.requires_continue
                else self._runner.resume(
                    adapter_name=adapter_name,
                    conversation_id=conversation_id,
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
            await self._runs.settle_resume(command.run_id, outcome)
            async for event in self._outcome_events(
                run_id=command.run_id,
                outcome=outcome,
            ):
                yield event
        except asyncio.CancelledError:
            await _finish_cleanup(
                self._runs.cancel(command.run_id, from_statuses={"running"})
            )
            raise
        except Exception:
            logger.exception(
                "Interaction resume failed: run=%s interaction=%s",
                command.run_id,
                command.interaction_id,
            )
            await self._runs.fail(
                command.run_id,
                error_code="interaction_finalize_failed",
                from_statuses={"running"},
            )
            yield run_event(
                "run.failed",
                command.run_id,
                {"code": "interaction_finalize_failed"},
            )

    @staticmethod
    def _resolved_event(command: ResolveInteractionCommand) -> RuntimeEvent:
        """构造不泄漏领域 Resolution 载荷的统一解决事件。"""
        outcome = command.payload.get("decision")
        return interaction_resolved_event(
            interaction_id=command.interaction_id,
            kind=command.kind,
            outcome=outcome if isinstance(outcome, str) else "submitted",
        ).bind(run_id=command.run_id)

    @staticmethod
    async def _outcome_events(
        *,
        run_id: int,
        outcome: GraphOutcome,
        completed_payload: JsonObject | None = None,
    ) -> AsyncIterator[RuntimeEvent]:
        """把 Graph Outcome 映射为唯一一套 Run/Interaction 事件。"""
        if outcome.status == "completed":
            yield run_event("run.completed", run_id, completed_payload)
            return
        interaction = outcome.interaction
        if interaction is None:
            raise InteractionStateError("Waiting Graph has no interaction")
        yield run_event(
            "run.suspended",
            run_id,
            {
                "interaction_id": interaction.interaction_id,
                "kind": interaction.kind,
            },
        )
        yield interaction_requested_event(
            interaction_id=interaction.interaction_id,
            kind=interaction.kind,
            payload=interaction.payload,
        ).bind(run_id=run_id)

    async def close_conversation(self, conversation_id: int, reason: str) -> None:
        """结束会话，并取消仍处于运行或等待状态的 Run。"""
        await self._runs.close_conversation(conversation_id, reason)

    async def delete_conversation(self, conversation_id: int) -> None:
        """删除会话业务记录及对应 checkpoint。"""
        async with database_module.db.session() as session:
            deleted = await self._repositories.create(session).conversations.delete(
                conversation_id
            )
            await session.commit()
        if deleted:
            await self._runner.delete_thread(conversation_id)

    async def delete_subject(self, adapter: str, subject: JsonObject) -> int:
        """删除绑定到一个不透明业务主体的全部会话。"""
        normalized = SubjectRef.model_validate(subject).model_dump(mode="json")
        async with database_module.db.session() as session:
            repository = self._repositories.create(session).conversations
            ids = await repository.ids_for_subject(adapter, normalized)
            for conversation_id in ids:
                await repository.delete(conversation_id)
            await session.commit()
        for conversation_id in ids:
            await self._runner.delete_thread(conversation_id)
        return len(ids)
