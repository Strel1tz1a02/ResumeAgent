"""管理可复用 AI 会话生命周期的内部应用服务。"""

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
from app.ai_chat.streaming.events import AiChatEvent
from app.ai_chat.tools.lifecycle import ToolLifecycle
from app.ai_chat.graph.state import AdapterInput, ApprovalInput
from app.ai_chat.tools.results import PendingToolResult
from app.ai_chat.types import JsonObject, SubjectRef, TargetRef

logger = logging.getLogger(__name__)


class AiChatService:
    """协调通用对话持久化、图执行和失败恢复。"""

    def __init__(
        self,
        registry: AdapterRegistry,
        runner: GraphRunner,
        tool_lifecycle: ToolLifecycle,
        repositories: RepositoryFactory,
    ) -> None:
        """组装无状态协作者；审批互斥由数据库 CAS 保证。"""
        self._registry = registry
        self._runner = runner
        self._tool_lifecycle = tool_lifecycle
        self._repositories = repositories

    async def create_conversation( self,adapter_name: str,subject: JsonObject,target: JsonObject,language: str = "zh", ) -> int:
        """校验并持久化会话。"""
        adapter = self._registry.get(adapter_name)
        binding = await adapter.validate_binding(SubjectRef.model_validate(subject), TargetRef.model_validate(target))
        async with database_module.db.session() as session:
            row = await self._repositories.create(session).conversations.create(
                adapter=adapter_name,
                subject=binding.subject.model_dump(mode="json"),
                target=binding.target.model_dump(mode="json"),
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
        """原子创建run，然后流式生成并结束助手消息。"""
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
        """为一次调用加载可序列化历史和待补传工具结果。"""
        async with database_module.db.session() as session:
            repositories = self._repositories.create(session)
            conversation = await repositories.conversations.get(conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(str(conversation_id))
            message_rows = await repositories.messages.list_completed(conversation_id)
            pending_rows = await repositories.tool_calls.pending_results(conversation_id)
            pending: list[PendingToolResult] = [
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
                "target": conversation.target,
                "language": conversation.language,
                "run_kind": kind,
                "tools_enabled": tools_enabled,
                "messages": [
                    {"role": row.role, "content": row.content} for row in message_rows
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
        resume: JsonObject | ApprovalInput | None = None,
        silent_failure: bool = False,
    ) -> AsyncIterator[AiChatEvent]:
        """持久化完整响应，或记录可安全恢复的失败。"""
        text = ""
        suspended = False
        proposal_event: AiChatEvent | None = None
        try:
            async for event in self._runner.stream(adapter_name=adapter_name, value=value, resume=resume):
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
        """完成审批后恢复同一检查点收尾，不额外调用模型。"""
        async with database_module.db.session() as session:
            repositories = self._repositories.create(session)
            repository = repositories.tool_calls
            call = await repository.get(proposal_id)
            if call is None:
                raise ToolCallNotFoundError(str(proposal_id))
            if call.status == "resolved":
                if (
                    call.client_resolution_id != client_resolution_id
                    or call.decision != decision
                ):
                    raise IdempotencyConflictError(client_resolution_id)
            elif call.status != "awaiting_approval":
                raise ProposalStateError(str(proposal_id))
            existing_resolution = await repository.get_by_resolution_id(
                call.conversation_id, client_resolution_id
            )
            if existing_resolution is not None and existing_resolution.id != call.id:
                raise IdempotencyConflictError(client_resolution_id)
            conversation = await repositories.conversations.get(call.conversation_id)
            run = await repositories.runs.get(call.run_id)
            if conversation is None or run is None:
                raise ProposalStateError(str(proposal_id))
            adapter = self._registry.get(conversation.adapter)
            handler = adapter.get_tool_handlers().get(call.tool_name)
            if handler is None:
                raise ProposalStateError(call.tool_name)
            subject = conversation.subject
            target = conversation.target
            adapter_name = conversation.adapter
            conversation_id = conversation.id
            run_id = run.id
            run_kind = run.kind
            if call.status == "resolved" and run.status == "completed":
                yield AiChatEvent(
                    "proposal.resolved",
                    {"proposal_id": proposal_id, "decision": decision},
                )
                return
            recover_interrupt = run.status != "suspended"

        if recover_interrupt:
            recovered = await self._runner.ensure_interrupted(
                adapter_name=adapter_name,
                conversation_id=conversation_id,
            )
            if not recovered:
                raise ProposalStateError(str(proposal_id))
            async with database_module.db.session() as session:
                repositories = self._repositories.create(session)
                transitioned = await repositories.runs.transition(
                    run_id,
                    from_statuses={"running", "cancelled"},
                    to_status="suspended",
                )
                if not transitioned:
                    raise ProposalStateError(str(proposal_id))
                await session.commit()

        tool_result = await self._tool_lifecycle.resolve(
            tool_call_id=proposal_id,
            decision=decision,
            handler=handler,
            subject=subject,
            target=target,
            client_resolution_id=client_resolution_id,
        )

        # 审批和业务写入已经在一个事务中完成。先解除前端审批态；Tool Result
        # 保持 pending，等下一条用户消息真正调用模型时再随上下文补传。
        yield AiChatEvent(
            "proposal.resolved",
            {"proposal_id": proposal_id, "decision": decision},
        )

        try:
            async with database_module.db.session() as session:
                repositories = self._repositories.create(session)
                transitioned = await repositories.runs.transition(
                    run_id,
                    from_statuses={"suspended"},
                    to_status="running",
                )
                if not transitioned:
                    await session.rollback()
                    return
                await session.commit()
        except Exception:
            logger.exception(
                "AI Chat could not start proposal finalization: proposal=%s",
                proposal_id,
            )
            return
        approval: ApprovalInput = {
            "tool_call_id": proposal_id,
            "decision": decision,
            "tool_result": tool_result,
        }
        try:
            value = await self._build_input(
                conversation_id,
                run_id,
                run_kind,
                False,
            )
        except Exception:
            logger.exception(
                "AI Chat could not prepare proposal finalization: proposal=%s",
                proposal_id,
            )
            async with database_module.db.session() as session:
                repositories = self._repositories.create(session)
                await repositories.runs.transition(
                    run_id,
                    from_statuses={"running"},
                    to_status="failed",
                    error_code="proposal_finalize_prepare_failed",
                )
                await session.commit()
            return
        continuation_message_id: int | None = None
        continuation_text = ""
        try:
            async for event in self._runner.stream(
                adapter_name=adapter_name,
                value=value,
                resume=approval,
            ):
                if event.event == "_graph.interrupted":
                    raise ProposalStateError("proposal finalization interrupted again")
                if event.event == "assistant.delta":
                    delta = event.data.get("text")
                    if isinstance(delta, str) and delta:
                        if continuation_message_id is None:
                            async with database_module.db.session() as session:
                                repositories = self._repositories.create(session)
                                assistant = await repositories.messages.create(
                                    conversation_id=conversation_id,
                                    run_id=run_id,
                                    role="assistant",
                                    content="",
                                    status="generating",
                                )
                                await session.commit()
                                continuation_message_id = assistant.id
                            yield AiChatEvent(
                                "assistant.started",
                                {"message_id": continuation_message_id},
                            )
                        continuation_text += delta
                yield event
            async with database_module.db.session() as session:
                repositories = self._repositories.create(session)
                transitioned = await repositories.runs.transition(
                    run_id,
                    from_statuses={"running"},
                    to_status="completed",
                )
                if not transitioned:
                    await session.rollback()
                    return
                if continuation_message_id is not None:
                    message = await session.get(
                        AiChatMessage, continuation_message_id
                    )
                    if message is not None:
                        message.content = continuation_text
                        await repositories.messages.finish(message, "completed")
                    delivered_calls = []
                    for item in value["pending_tool_results"]:
                        delivered = await repositories.tool_calls.get(
                            int(item["tool_call_id"])
                        )
                        if (
                            delivered is not None
                            and delivered.delivery_status == "pending"
                        ):
                            delivered_calls.append(delivered)
                    await repositories.tool_calls.mark_consumed(delivered_calls)
                await session.commit()
            if continuation_message_id is not None:
                yield AiChatEvent(
                    "assistant.completed",
                    {
                        "message_id": continuation_message_id,
                        "content": continuation_text,
                    },
                )
        except Exception:
            logger.exception(
                "AI Chat proposal finalization failed: proposal=%s",
                proposal_id,
            )
            if continuation_message_id is not None:
                await self._finish_interrupted_run(
                    run_id,
                    continuation_message_id,
                    "failed",
                    "proposal_finalize_failed",
                    continuation_text,
                )
            else:
                async with database_module.db.session() as session:
                    repositories = self._repositories.create(session)
                    await repositories.runs.transition(
                        run_id,
                        from_statuses={"running"},
                        to_status="failed",
                        error_code="proposal_finalize_failed",
                    )
                    await session.commit()

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
