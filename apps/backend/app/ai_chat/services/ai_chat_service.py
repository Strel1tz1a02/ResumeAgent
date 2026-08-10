"""管理可复用 AI Chat 会话生命周期的内部应用服务。"""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Literal

from sqlalchemy.exc import IntegrityError

from app import database as database_module
from app.ai_chat.errors import (
    ConversationEndedError,
    ConversationNotFoundError,
    IdempotencyConflictError,
    ProposalStateError,
    RunInProgressError,
    ToolCallNotFoundError,
)
from app.ai_chat.graph.runner import GraphRunner
from app.ai_chat.models import AiChatMessage
from app.ai_chat.adapters import AdapterRegistry
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.streaming.events import AiChatEvent, tool_result_event
from app.ai_chat.graph.state import AdapterInput
from app.ai_chat.tools.types import ApprovalDecision
from app.ai_chat.types import JsonObject, ScopeRef, SubjectRef

logger = logging.getLogger(__name__)


async def _await_shielded_task(
    task: asyncio.Task[None],
) -> asyncio.CancelledError | None:
    """屏蔽外层取消，把独立事务推进到确定的成功或失败。"""
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
        except Exception:
            break
    if task.cancelled():
        if cancellation is not None:
            raise cancellation
        await task
    error = task.exception()
    if error is not None:
        if cancellation is not None:
            raise cancellation from None
        raise error
    return cancellation


class AiChatService:
    """协调通用对话持久化、图执行和失败恢复。"""

    def __init__(
        self,
        registry: AdapterRegistry,
        runner: GraphRunner,
        repositories: RepositoryFactory,
    ) -> None:
        """组装无状态协作者；审批互斥由数据库原子状态转换保证。"""
        self._registry = registry
        self._runner = runner
        self._repositories = repositories

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
        """原子创建运行记录，然后流式生成并结束助手消息。"""
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
                        yield AiChatEvent( "message.replayed", {"message_id": existing.id})
                        return
                    
                if await runs_repository.current(conversation_id) is not None:
                    raise RunInProgressError(str(conversation_id))
                run_row = await runs_repository.create(
                    conversation_id=conversation_id,
                    kind=kind,
                    tools_enabled=True,
                )

                if user_content is not None:
                    await message_repository.create(
                        conversation_id=conversation_id,
                        run_id=run_row.id,
                        role="user",
                        content=user_content,
                        status="completed",
                        client_message_id=client_message_id,
                    )

                assistant_row = await message_repository.create(
                    conversation_id=conversation_id,
                    run_id=run_row.id,
                    role="assistant",
                    content="",
                    status="generating",
                )
                await session.commit()
                value = await self._build_input(
                    conversation_id,
                    run_row.id,
                    kind,
                    True,
                )
                adapter_name = conversation.adapter
                assistant_id = assistant_row.id
        except IntegrityError as exc:
            raise RunInProgressError(str(conversation_id)) from exc

        yield AiChatEvent("assistant.started", {"message_id": assistant_id})
        async for event in self._execute(adapter_name=adapter_name, value=value,assistant_id=assistant_id,):
            yield event

    async def _build_input(
        self,
        conversation_id: int,
        run_id: int,
        kind: str,
        tools_enabled: bool,
    ) -> AdapterInput:
        """为一次调用加载当前 Run 消息和待补传工具结果。"""
        async with database_module.db.session() as session:
            repositories = self._repositories.create(session)
            conversation = await repositories.conversations.get(conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(str(conversation_id))
            current_message_rows = await repositories.messages.list_completed_for_run(run_id)
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
            value: AdapterInput = {
                "conversation_id": conversation.id,
                "run_id": run_id,
                "subject": conversation.subject,
                "scope": conversation.scope,
                "language": conversation.language,
                "run_kind": kind,
                "tools_enabled": tools_enabled,
                "messages": [
                    {"role": row.role, "content": row.content}
                    for row in current_message_rows
                ],
                "pending_tool_results": pending,
            }
            return value

    async def _execute(
        self,
        *,
        adapter_name: str,
        value: AdapterInput,
        assistant_id: int,
        silent_failure: bool = False,
    ) -> AsyncIterator[AiChatEvent]:
        """持久化完整响应，或记录可安全恢复的失败。"""
        text = ""
        suspended = False
        proposal_event: AiChatEvent | None = None
        try:
            async for event in self._runner.stream(adapter_name=adapter_name, value=value):
                if event.event == "assistant.delta":
                    delta = event.data.get("text")
                    if isinstance(delta, str):
                        text += delta
                if event.event == "proposal.requested":
                    # 工具提案已经落库，但此时图可能尚未完成中断。
                    # 先暂存，等检查点和暂停状态都提交后再发给前端。
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
        """只恢复审批决定；实际执行由图中的执行节点完成。"""
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
            if run.status == "running" and call.status != "resolved":
                raise RunInProgressError(str(run.id))
            recover_interrupt = run.status != "suspended"

        approval: ApprovalDecision = {
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
                        if event.event == "proposal.requested":
                            if event.data.get("proposal_id") != proposal_id:
                                raise ProposalStateError(
                                    "Recovered proposal request has a different identity"
                                )
                            continue
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

            claim_committed = False

            async def claim_run() -> None:
                nonlocal claim_committed
                async with database_module.db.session() as session:
                    repositories = self._repositories.create(session)
                    transitioned = await repositories.runs.transition(
                        run_id,
                        from_statuses={"suspended"},
                        to_status="running",
                    )
                    if not transitioned:
                        raise ProposalStateError(str(proposal_id))
                    await session.commit()
                    claim_committed = True

            claim_task = asyncio.create_task(
                claim_run(),
                name=f"ai-chat-run-{run_id}-claim",
            )
            try:
                claim_cancellation = await _await_shielded_task(claim_task)
            finally:
                claimed_running = claim_committed
            if claim_cancellation is not None:
                raise claim_cancellation

            resumed_events: list[AiChatEvent] = []
            async for event in self._runner.resume(
                adapter_name=adapter_name,
                conversation_id=conversation_id,
                approval=approval,
            ):
                if event.event == "_graph.interrupted":
                    raise ProposalStateError("proposal finalization interrupted again")
                if event.event == "proposal.requested":
                    if event.data.get("proposal_id") != proposal_id:
                        raise ProposalStateError(
                            "Resumed proposal request has a different identity"
                        )
                    continue
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
            for event in resumed_events:
                yield event
        except asyncio.CancelledError:
            if claimed_running:
                async def cancel_claimed_run() -> None:
                    async with database_module.db.session() as session:
                        repositories = self._repositories.create(session)
                        await repositories.runs.transition(
                            run_id,
                            from_statuses={"running"},
                            to_status="cancelled",
                        )
                        await session.commit()

                cleanup_task = asyncio.create_task(
                    cancel_claimed_run(),
                    name=f"ai-chat-run-{run_id}-cancel",
                )
                try:
                    await _await_shielded_task(cleanup_task)
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception(
                        "AI Chat cancelled Run cleanup failed: run=%s",
                        run_id,
                    )
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
