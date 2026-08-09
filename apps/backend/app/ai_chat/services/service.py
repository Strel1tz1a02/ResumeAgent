"""管理可复用 AI 会话生命周期的内部应用服务。"""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Literal

from sqlalchemy.exc import IntegrityError

from app import database as database_module
from app.ai_chat.errors import (
    ContextFullError,
    ConversationEndedError,
    ConversationNotFoundError,
    IdempotencyConflictError,
    MemoryCompactionError,
    ProposalStateError,
    RunInProgressError,
    ToolCallNotFoundError,
)
from app.ai_chat.graph.runner import GraphRunner
from app.ai_chat.context import ContextPlanner, PreparedContext
from app.ai_chat.memory.service import MemoryMaintainer, MemoryService
from app.ai_chat.models import AiChatMessage
from app.ai_chat.adapters import AdapterRegistry
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.streaming.events import AiChatEvent, tool_result_event
from app.ai_chat.graph.state import AdapterInput, ApprovalInput, BaseState
from app.ai_chat.types import JsonObject, ScopeRef, SubjectRef

logger = logging.getLogger(__name__)


class AiChatService:
    """协调通用对话持久化、图执行和失败恢复。"""

    def __init__(
        self,
        registry: AdapterRegistry,
        runner: GraphRunner,
        repositories: RepositoryFactory,
    ) -> None:
        """组装无状态协作者；审批互斥由数据库 CAS 保证。"""
        self._registry = registry
        self._runner = runner
        self._repositories = repositories
        self._memory = MemoryService(repositories)
        self._planner = ContextPlanner(runner, repositories, self._memory)
        self._maintainer = MemoryMaintainer(self._memory)

    async def recover_stale_preflight(self) -> int:
        """应用启动时解除无 Message 的遗留 Run 占用。"""
        async with database_module.db.session() as session:
            count = await self._repositories.create(session).runs.recover_stale_preflight()
            await session.commit()
            return count

    async def close(self) -> None:
        """等待或取消本进程持有的后台记忆任务。"""
        await self._maintainer.close()

    async def create_conversation(
        self,
        adapter_name: str,
        subject: JsonObject,
        scope: JsonObject,
        language: str = "zh",
    ) -> int:
        """校验并持久化会话。"""
        adapter = self._registry.get(adapter_name)
        binding = await adapter.validate_binding(
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
            await self._repositories.create(session).memory.get_or_create(row.id)
            await session.commit()
            return row.id

    async def stream_opening(self, conversation_id: int) -> AsyncIterator[AiChatEvent]:
        """启动并流式返回适配器的开场轮次。"""
        async for event in self._stream_new_run(
            conversation_id=conversation_id,
            kind="opening",
            user_content=None,
            client_message_id=None,
        ):
            yield event

    async def stream_message(self, conversation_id: int, content: str, client_message_id: str) -> AsyncIterator[AiChatEvent]:
        """持久化幂等用户消息并流式执行一次模型调用。"""
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
    ) -> AsyncIterator[AiChatEvent]:
        """先保留 Run，再完成有界上下文预检，最后创建可见消息。"""
        replay_event: AiChatEvent | None = None
        try:
            async with database_module.db.session() as session:
                repositories = self._repositories.create(session)
                conversations_repository = repositories.conversations
                message_repository = repositories.messages
                runs_repository = repositories.runs
                conversation = await conversations_repository.get(conversation_id)
                if conversation is None:
                    raise ConversationNotFoundError(str(conversation_id))
                if conversation.status != "active":
                    raise ConversationEndedError(str(conversation_id))
                
                self._registry.get(conversation.adapter)
                if client_message_id is not None:
                    existing = await message_repository.get_by_client_id(conversation_id, client_message_id)
                    if existing is not None:
                        if existing.content != user_content:
                            raise IdempotencyConflictError(client_message_id)
                        replay_event = AiChatEvent(
                            "message.replayed", {"message_id": existing.id}
                        )
                    if replay_event is not None:
                        await session.rollback()
                    else:
                        if await runs_repository.current(conversation_id) is not None:
                            raise RunInProgressError(str(conversation_id))
                        run_row = await runs_repository.create(
                            conversation_id=conversation_id,
                            kind=kind,
                            tools_enabled=True,
                        )
                        await session.commit()
                        run_id = run_row.id
                        adapter_name = conversation.adapter
        except IntegrityError as exc:
            raise RunInProgressError(str(conversation_id)) from exc
        if replay_event is not None:
            yield replay_event
            return

        assistant_id: int | None = None
        prepared: PreparedContext | None = None
        try:
            async for item in self._planner.prepare(
                conversation_id=conversation_id,
                run_id=run_id,
                kind=kind,
                user_content=user_content,
                tools_enabled=True,
            ):
                if isinstance(item, AiChatEvent):
                    yield item
                else:
                    prepared = item
            if prepared is None:
                raise RuntimeError("context planner produced no prepared context")
            async with database_module.db.session() as session:
                repositories = self._repositories.create(session)
                run = await repositories.runs.get(run_id)
                if run is None or run.status != "running":
                    raise RunInProgressError(str(conversation_id))
                if user_content is not None:
                    await repositories.messages.create(
                        conversation_id=conversation_id,
                        run_id=run_id,
                        role="user",
                        content=user_content,
                        status="completed",
                        client_message_id=client_message_id,
                    )
                assistant = await repositories.messages.create(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    role="assistant",
                    content="",
                    status="generating",
                )
                assistant_id = assistant.id
                await session.commit()
            yield AiChatEvent(
                "context.usage",
                {
                    "used_tokens": prepared.used_tokens,
                    "budget_tokens": prepared.budget_tokens,
                    "percent": min(
                        100,
                        round(prepared.used_tokens * 100 / prepared.budget_tokens),
                    ),
                },
            )
            yield AiChatEvent("assistant.started", {"message_id": assistant_id})
            async for event in self._execute(
                adapter_name=adapter_name,
                value=prepared.value,
                prepared_state=prepared.state,
                assistant_id=assistant_id,
            ):
                yield event
        except ContextFullError as exc:
            await self._finish_preflight_run(run_id, "failed", exc.code)
            if exc.used_tokens is not None and exc.budget_tokens is not None:
                yield AiChatEvent(
                    "context.usage",
                    {
                        "used_tokens": exc.used_tokens,
                        "budget_tokens": exc.budget_tokens,
                        "percent": min(
                            100,
                            round(exc.used_tokens * 100 / exc.budget_tokens),
                        ),
                    },
                )
            yield AiChatEvent(
                "run.failed", {"code": exc.code, "reason": exc.reason}
            )
        except MemoryCompactionError:
            logger.exception("AI Chat memory compaction failed")
            await self._finish_preflight_run(
                run_id, "failed", "memory_compaction_failed"
            )
            yield AiChatEvent(
                "run.failed", {"code": "memory_compaction_failed"}
            )
        except asyncio.CancelledError:
            await self._finish_preflight_run(run_id, "cancelled", None)
            raise
        except Exception as exc:
            logger.exception(
                "AI Chat context preparation failed: conversation=%s run=%s",
                conversation_id,
                run_id,
            )
            code = getattr(exc, "code", "context_preparation_failed")
            await self._finish_preflight_run(run_id, "failed", code)
            yield AiChatEvent("run.failed", {"code": code})
        finally:
            await self._cancel_unfinished_admission(run_id, assistant_id)

    async def _execute(
        self,
        *,
        adapter_name: str,
        value: AdapterInput,
        prepared_state: BaseState,
        assistant_id: int,
        silent_failure: bool = False,
    ) -> AsyncIterator[AiChatEvent]:
        """持久化完整响应，或记录可安全恢复的失败。"""
        text = ""
        suspended = False
        proposal_event: AiChatEvent | None = None
        try:
            async for event in self._runner.stream(
                adapter_name=adapter_name,
                value=value,
                prepared_state=prepared_state,
            ):
                if event.event == "assistant.delta":
                    delta = event.data.get("text")
                    if isinstance(delta, str):
                        text += delta
                if event.event == "proposal.requested":
                    # Tool 提案已经落库，但此时 Graph 可能尚未完成 interrupt。
                    # 先暂存，等 checkpoint 和 run.suspended 都提交后再发给前端。
                    proposal_event = event
                    continue
                if event.event == "_graph.interrupted":
                    suspended = True
                    break
                yield event

            async with database_module.db.session() as session:
                message = await session.get(AiChatMessage, assistant_id)
                repositories = self._repositories.create(session)
                transitioned = await repositories.runs.transition(
                    value["run_id"],
                    from_statuses={"running"},
                    to_status="suspended" if suspended else "completed",
                )
                if not transitioned:
                    await session.rollback()
                    return
                if message is not None:
                    message.content = text
                    message_status = (
                        "cancelled" if suspended and not text else "completed"
                    )
                    await repositories.messages.finish(message, message_status)
                delivered_ids = {
                    int(item["tool_call_id"])
                    for item in value["pending_tool_results"]
                }
                delivered_calls = []
                for tool_call_id in delivered_ids:
                    call = await repositories.tool_calls.get(tool_call_id)
                    if call is not None and call.delivery_status == "pending":
                        delivered_calls.append(call)
                await repositories.tool_calls.mark_consumed(delivered_calls)
                await session.commit()
            if suspended:
                if proposal_event is None:
                    raise ProposalStateError(
                        "proposal.requested must be emitted before graph interrupt"
                    )
                yield proposal_event
            else:
                self._maintainer.schedule(value["conversation_id"])
                yield AiChatEvent("assistant.completed", {"message_id": assistant_id, "content": text},)
        except asyncio.CancelledError:
            await self._finish_interrupted_run(
                value["run_id"], assistant_id, "cancelled", None, text
            )
            raise
        except Exception as exc:
            logger.exception(
                "AI Chat run failed: conversation=%s run=%s",
                value["conversation_id"],
                value["run_id"],
            )
            code = getattr(exc, "code", "graph_execution_failed")
            await self._finish_interrupted_run(
                value["run_id"], assistant_id, "failed", code, text
            )
            if not silent_failure:
                yield AiChatEvent("run.failed", {"code": code})

    async def _finish_preflight_run(
        self,
        run_id: int,
        status: Literal["failed", "cancelled"],
        code: str | None,
    ) -> None:
        async with database_module.db.session() as session:
            await self._repositories.create(session).runs.transition(
                run_id,
                from_statuses={"running"},
                to_status=status,
                error_code=code,
            )
            await session.commit()

    async def _cancel_unfinished_admission(
        self, run_id: int, assistant_id: int | None
    ) -> None:
        """覆盖任意 yield 后调用方关闭生成器的收敛路径。"""
        async with database_module.db.session() as session:
            repositories = self._repositories.create(session)
            transitioned = await repositories.runs.transition(
                run_id,
                from_statuses={"running"},
                to_status="cancelled",
            )
            if transitioned and assistant_id is not None:
                await repositories.messages.cancel_generating(run_id)
            await session.commit()

    async def _finish_interrupted_run(
        self,
        run_id: int,
        assistant_id: int,
        status: Literal["failed", "cancelled"],
        code: str | None,
        content: str,
    ) -> None:
        """持久化部分输出并结束失败或取消的运行。"""
        async with database_module.db.session() as session:
            message = await session.get(AiChatMessage, assistant_id)
            repositories = self._repositories.create(session)
            transitioned = await repositories.runs.transition(
                run_id,
                from_statuses={"running", "suspended"},
                to_status=status,
                error_code=code,
            )
            if not transitioned:
                await session.rollback()
                return
            if message is not None:
                message.content = content
                await repositories.messages.finish(message, status)
            await session.commit()

    async def resolve_proposal(
        self,
        proposal_id: int,
        decision: Literal["approve", "reject"],
        client_resolution_id: str,
    ) -> AsyncIterator[AiChatEvent]:
        """只恢复审批决定；实际执行由 Graph executor 完成。"""
        async with database_module.db.session() as session:
            repositories = self._repositories.create(session)
            call = await repositories.tool_calls.get(proposal_id)
            if call is None:
                raise ToolCallNotFoundError(str(proposal_id))
            conversation = await repositories.conversations.get(call.conversation_id)
            run = await repositories.runs.get(call.run_id)
            if conversation is None or run is None:
                raise ProposalStateError(str(proposal_id))
            self._registry.get(conversation.adapter)
            adapter_name = conversation.adapter
            conversation_id = conversation.id
            run_id = run.id
            if call.status in {"approved", "resolved"}:
                if (
                    call.decision != decision
                    or call.client_resolution_id != client_resolution_id
                ):
                    raise IdempotencyConflictError(client_resolution_id)
                if call.status == "resolved" and run.status == "completed":
                    yield AiChatEvent(
                        "proposal.resolved",
                        {"proposal_id": proposal_id, "decision": decision},
                    )
                    return
            elif call.status != "awaiting_approval":
                raise ProposalStateError(str(proposal_id))
            if run.status == "completed":
                raise ProposalStateError(str(proposal_id))
            recover_interrupt = run.status != "suspended"

        approval: ApprovalInput = {
            "tool_call_id": proposal_id,
            "decision": decision,
            "client_resolution_id": client_resolution_id,
        }
        claimed_running = False
        try:
            recovery = await self._runner.ensure_interrupted(
                adapter_name=adapter_name,
                conversation_id=conversation_id,
                approval=approval,
            )
            if not recovery.interrupted:
                async with database_module.db.session() as session:
                    repositories = self._repositories.create(session)
                    resolved_call = await repositories.tool_calls.get(proposal_id)
                    if (
                        resolved_call is None
                        or resolved_call.status != "resolved"
                        or resolved_call.decision != decision
                        or resolved_call.client_resolution_id
                        != client_resolution_id
                    ):
                        raise ProposalStateError(str(proposal_id))
                    if resolved_call.tool_result is None:
                        raise ProposalStateError(
                            "Resolved Tool Call has no durable result"
                        )
                    durable_tool_event = tool_result_event(
                        tool_name=resolved_call.tool_name,
                        tool_call_id=resolved_call.id,
                        result=dict(resolved_call.tool_result),
                    )
                    resolution_event = AiChatEvent(
                        "proposal.resolved",
                        {"proposal_id": proposal_id, "decision": decision},
                    )
                    recovered_events: list[AiChatEvent] = []
                    for event in recovery.events:
                        if event.event == "proposal.resolved":
                            if event.data != resolution_event.data:
                                raise ProposalStateError(
                                    "Recovered approval event has a different identity"
                                )
                            continue
                        if event.data.get("tool_call_id") == proposal_id:
                            if event != durable_tool_event:
                                raise ProposalStateError(
                                    "Recovered Tool Result differs from durable result"
                                )
                            continue
                        recovered_events.append(event)
                    transitioned = await repositories.runs.transition(
                        run_id,
                        from_statuses={
                            "running",
                            "suspended",
                            "cancelled",
                            "failed",
                        },
                        to_status="completed",
                    )
                    if not transitioned:
                        current_run = await repositories.runs.get(run_id)
                        if current_run is None or current_run.status != "completed":
                            raise ProposalStateError(str(proposal_id))
                    await session.commit()
                self._maintainer.schedule(conversation_id)
                for event in (
                    resolution_event,
                    *recovered_events,
                    durable_tool_event,
                ):
                    yield event
                return

            if recover_interrupt:
                async with database_module.db.session() as session:
                    repositories = self._repositories.create(session)
                    transitioned = await repositories.runs.transition(
                        run_id,
                        from_statuses={"running", "cancelled", "failed"},
                        to_status="suspended",
                    )
                    if not transitioned:
                        current_run = await repositories.runs.get(run_id)
                        if current_run is None or current_run.status != "suspended":
                            raise ProposalStateError(str(proposal_id))
                    await session.commit()

            async with database_module.db.session() as session:
                repositories = self._repositories.create(session)
                transitioned = await repositories.runs.transition(
                    run_id,
                    from_statuses={"suspended"},
                    to_status="running",
                )
                if not transitioned:
                    raise ProposalStateError(str(proposal_id))
                claimed_running = True
                await session.commit()

            resumed_events: list[AiChatEvent] = []
            async for event in self._runner.resume(
                adapter_name=adapter_name,
                conversation_id=conversation_id,
                approval=approval,
            ):
                if event.event == "_graph.interrupted":
                    raise ProposalStateError("proposal finalization interrupted again")
                resumed_events.append(event)
            if not resumed_events:
                raise ProposalStateError("proposal finalization produced no events")
            async with database_module.db.session() as session:
                repositories = self._repositories.create(session)
                transitioned = await repositories.runs.transition(
                    run_id,
                    from_statuses={"running"},
                    to_status="completed",
                )
                if not transitioned:
                    raise ProposalStateError(str(proposal_id))
                await session.commit()
            self._maintainer.schedule(conversation_id)
            for event in resumed_events:
                yield event
        except asyncio.CancelledError:
            if claimed_running:
                async with database_module.db.session() as session:
                    repositories = self._repositories.create(session)
                    await repositories.runs.transition(
                        run_id,
                        from_statuses={"running"},
                        to_status="cancelled",
                    )
                    await session.commit()
            raise
        except IdempotencyConflictError:
            if claimed_running:
                async with database_module.db.session() as session:
                    repositories = self._repositories.create(session)
                    await repositories.runs.transition(
                        run_id,
                        from_statuses={"running"},
                        to_status="suspended",
                    )
                    await session.commit()
            raise
        except (ProposalStateError, ToolCallNotFoundError):
            if not claimed_running:
                raise
            async with database_module.db.session() as session:
                repositories = self._repositories.create(session)
                await repositories.runs.transition(
                    run_id,
                    from_statuses={"running"},
                    to_status="failed",
                    error_code="proposal_finalize_failed",
                )
                await session.commit()
            yield AiChatEvent(
                "run.failed",
                {"code": "proposal_finalize_failed"},
            )
        except Exception:
            logger.exception(
                "AI Chat proposal finalization failed: proposal=%s",
                proposal_id,
            )
            if claimed_running:
                async with database_module.db.session() as session:
                    repositories = self._repositories.create(session)
                    await repositories.runs.transition(
                        run_id,
                        from_statuses={"running"},
                        to_status="failed",
                        error_code="proposal_finalize_failed",
                    )
                    await session.commit()
            yield AiChatEvent(
                "run.failed",
                {"code": "proposal_finalize_failed"},
            )

    async def close_conversation(self, conversation_id: int, reason: str) -> None:
        """结束空闲会话并保留其历史记录。"""
        async with database_module.db.session() as session:
            repositories = self._repositories.create(session)
            conversations = repositories.conversations
            conversation = await conversations.get(conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(str(conversation_id))
            current = await repositories.runs.current(conversation_id)
            if current is not None:
                transitioned = await repositories.runs.transition(
                    current.id,
                    from_statuses={"running", "suspended"},
                    to_status="cancelled",
                )
                if transitioned:
                    await repositories.messages.cancel_generating(current.id)
            await conversations.end(conversation, reason)
            await session.commit()

    async def delete_conversation(self, conversation_id: int) -> None:
        """删除一个会话及其检查点线程。"""
        async with database_module.db.session() as session:
            deleted = await self._repositories.create(session).conversations.delete(
                conversation_id
            )
            await session.commit()
        if deleted:
            await self._runner.delete_thread(conversation_id)

    async def delete_subject(self, adapter: str, subject: JsonObject) -> int:
        """删除绑定到不透明业务主体的全部会话。"""
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
