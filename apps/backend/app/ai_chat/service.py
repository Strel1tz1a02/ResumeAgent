"""Internal application service for reusable AI conversation lifecycles."""

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
from app.ai_chat.registry import AdapterRegistry
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.streaming.events import AiChatEvent
from app.ai_chat.tools.lifecycle import ToolLifecycle
from app.ai_chat.types import (
    AdapterInput,
    ApprovalInput,
    JsonObject,
    PendingToolResult,
    SubjectRef,
    TargetRef,
)

logger = logging.getLogger(__name__)


class AiChatService:
    """Coordinate generic chat persistence, Graph execution, and recovery."""

    def __init__(
        self,
        registry: AdapterRegistry,
        runner: GraphRunner,
        tool_lifecycle: ToolLifecycle,
        repositories: RepositoryFactory,
    ) -> None:
        """Compose stateless collaborators and process-local approval locks."""
        self._registry = registry
        self._runner = runner
        self._tool_lifecycle = tool_lifecycle
        self._repositories = repositories
        self._resolution_locks: dict[int, asyncio.Lock] = {}

    async def create_conversation(
        self,
        adapter: str,
        subject: JsonObject,
        target: JsonObject,
        language: str = "zh",
    ) -> int:
        """Validate the business binding before persisting a conversation."""
        instance = self._registry.get(adapter)
        binding = await instance.validate_binding(
            SubjectRef.model_validate(subject), TargetRef.model_validate(target)
        )
        async with database_module.db.session() as session:
            row = await self._repositories.create(session).conversations.create(
                adapter=adapter,
                subject=binding.subject.model_dump(mode="json"),
                target=binding.target.model_dump(mode="json"),
                language=language or "zh",
            )
            await session.commit()
            return row.id

    async def stream_opening(self, conversation_id: int) -> AsyncIterator[AiChatEvent]:
        """Start and stream the Adapter's opening turn."""
        async for event in self._stream_new_run(
            conversation_id=conversation_id,
            kind="opening",
            user_content=None,
            client_message_id=None,
        ):
            yield event

    async def stream_message(
        self, conversation_id: int, content: str, client_message_id: str
    ) -> AsyncIterator[AiChatEvent]:
        """Persist an idempotent user message and stream one model invocation."""
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
        """Atomically open a run, then stream and finalize its assistant message."""
        try:
            async with database_module.db.session() as session:
                repositories = self._repositories.create(session)
                conversations = repositories.conversations
                messages = repositories.messages
                runs = repositories.runs
                conversation = await conversations.get(conversation_id)
                if conversation is None:
                    raise ConversationNotFoundError(str(conversation_id))
                if conversation.status != "active":
                    raise ConversationEndedError(str(conversation_id))
                self._registry.get(conversation.adapter)
                if client_message_id is not None:
                    existing = await messages.get_by_client_id(
                        conversation_id, client_message_id
                    )
                    if existing is not None:
                        if existing.content != user_content:
                            raise IdempotencyConflictError(client_message_id)
                        yield AiChatEvent(
                            "message.replayed", {"message_id": existing.id}
                        )
                        return
                if await runs.current(conversation_id) is not None:
                    raise RunInProgressError(str(conversation_id))
                run = await runs.create(
                    conversation_id=conversation_id,
                    kind=kind,
                    tools_enabled=True,
                )
                user: AiChatMessage | None = None
                if user_content is not None:
                    user = await messages.create(
                        conversation_id=conversation_id,
                        run_id=run.id,
                        role="user",
                        content=user_content,
                        status="completed",
                        client_message_id=client_message_id,
                    )
                assistant = await messages.create(
                    conversation_id=conversation_id,
                    run_id=run.id,
                    role="assistant",
                    content="",
                    status="generating",
                )
                await session.commit()
                value = await self._build_input(
                    conversation_id, run.id, kind, True, user.id if user else None
                )
                adapter_name = conversation.adapter
                assistant_id = assistant.id
        except IntegrityError as exc:
            raise RunInProgressError(str(conversation_id)) from exc

        yield AiChatEvent("assistant.started", {"message_id": assistant_id})
        async for event in self._execute(
            adapter_name=adapter_name,
            value=value,
            assistant_id=assistant_id,
        ):
            yield event

    async def _build_input(
        self,
        conversation_id: int,
        run_id: int,
        kind: str,
        tools_enabled: bool,
        user_message_id: int | None = None,
        approval: ApprovalInput | None = None,
    ) -> AdapterInput:
        """Load serializable history and pending Tool Results for one invocation."""
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
                "adapter": conversation.adapter,
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
            if user_message_id is not None:
                value["user_message_id"] = user_message_id
            if approval is not None:
                value["approval"] = approval
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
        """Persist a complete response or a safely resumable failure."""
        text = ""
        suspended = False
        proposal_emitted = False
        try:
            async for event in self._runner.stream(
                adapter_name=adapter_name, value=value, resume=resume
            ):
                if event.event == "assistant.delta":
                    delta = event.data.get("text")
                    if isinstance(delta, str):
                        text += delta
                if event.event == "proposal.requested":
                    proposal_emitted = True
                if event.event == "_graph.interrupted":
                    suspended = True
                    payload = event.data.get("payload")
                    if not proposal_emitted and isinstance(payload, dict):
                        if isinstance(payload.get("event"), str):
                            body = payload.get("data")
                            yield AiChatEvent(
                                payload["event"],
                                body if isinstance(body, dict) else {},
                            )
                        else:
                            yield AiChatEvent("proposal.requested", payload)
                    break
                yield event
            async with database_module.db.session() as session:
                message = await session.get(AiChatMessage, assistant_id)
                repositories = self._repositories.create(session)
                run = await repositories.runs.get(value["run_id"])
                if message is not None:
                    message.content = text
                    await repositories.messages.finish(message, "completed")
                if run is not None:
                    await repositories.runs.set_status(
                        run, "suspended" if suspended else "completed"
                    )
                if not suspended:
                    calls = await repositories.tool_calls.pending_results(
                        value["conversation_id"]
                    )
                    await repositories.tool_calls.mark_consumed(calls)
                await session.commit()
            if not suspended:
                yield AiChatEvent(
                    "assistant.completed",
                    {"message_id": assistant_id, "content": text},
                )
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
        """Persist partial output and terminate a failed or cancelled run."""
        async with database_module.db.session() as session:
            message = await session.get(AiChatMessage, assistant_id)
            repositories = self._repositories.create(session)
            run = await repositories.runs.get(run_id)
            if message is not None:
                message.content = content
                await repositories.messages.finish(message, status)
            if run is not None:
                await repositories.runs.set_status(run, status, code)
            await session.commit()

    async def resolve_proposal(
        self,
        proposal_id: int,
        decision: Literal["approve", "reject"],
        client_resolution_id: str,
    ) -> AsyncIterator[AiChatEvent]:
        """Resolve once, then immediately resume the same checkpoint without Tools."""
        lock = self._resolution_locks.setdefault(proposal_id, asyncio.Lock())
        async with lock:
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
                    yield AiChatEvent(
                        "proposal.resolved",
                        {"proposal_id": proposal_id, "decision": decision},
                    )
                    return
                if call.status != "awaiting_approval":
                    raise ProposalStateError(str(proposal_id))
                existing_resolution = await repository.get_by_resolution_id(
                    call.conversation_id, client_resolution_id
                )
                if existing_resolution is not None and existing_resolution.id != call.id:
                    raise IdempotencyConflictError(client_resolution_id)
                conversation = await repositories.conversations.get(
                    call.conversation_id
                )
                run = await repositories.runs.get(call.run_id)
                if conversation is None or run is None or run.status != "suspended":
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

            tool_result = await self._tool_lifecycle.resolve(
                tool_call_id=proposal_id,
                decision=decision,
                handler=handler,
                subject=subject,
                target=target,
            )
            async with database_module.db.session() as session:
                repositories = self._repositories.create(session)
                repository = repositories.tool_calls
                call = await repository.get(proposal_id)
                if call is None:
                    raise ProposalStateError(str(proposal_id))
                await repository.resolve(
                    call,
                    decision=decision,
                    tool_result=tool_result,
                    client_resolution_id=client_resolution_id,
                )
                await session.commit()

            try:
                async with database_module.db.session() as session:
                    repositories = self._repositories.create(session)
                    run = await repositories.runs.get(run_id)
                    if run is None:
                        raise ProposalStateError(str(proposal_id))
                    await repositories.runs.set_status(run, "completed")
                    continuation = await repositories.runs.create(
                        conversation_id=conversation_id,
                        kind="post_tool_continuation",
                        tools_enabled=False,
                    )
                    assistant = await repositories.messages.create(
                        conversation_id=conversation_id,
                        run_id=continuation.id,
                        role="assistant",
                        content="",
                        status="generating",
                    )
                    await session.commit()
                    continuation_run_id = continuation.id
            except Exception:
                logger.exception(
                    "AI Chat could not start post-tool continuation: proposal=%s",
                    proposal_id,
                )
                async with database_module.db.session() as session:
                    repositories = self._repositories.create(session)
                    run = await repositories.runs.get(run_id)
                    if run is not None and run.status in {"running", "suspended"}:
                        await repositories.runs.set_status(run, "failed")
                    await session.commit()
                return

            yield AiChatEvent(
                "proposal.resolved",
                {"proposal_id": proposal_id, "decision": decision},
            )
            approval: ApprovalInput = {
                "tool_call_id": proposal_id,
                "decision": decision,
                "tool_result": tool_result,
            }
            value = await self._build_input(
                conversation_id,
                continuation_run_id,
                "post_tool_continuation",
                False,
                approval=approval,
            )
            async for event in self._execute(
                adapter_name=adapter_name,
                value=value,
                assistant_id=assistant.id,
                resume=approval,
                silent_failure=True,
            ):
                yield event
        self._resolution_locks.pop(proposal_id, None)

    async def close_conversation(self, conversation_id: int, reason: str) -> None:
        """End an idle conversation while preserving its history."""
        async with database_module.db.session() as session:
            repositories = self._repositories.create(session)
            conversations = repositories.conversations
            conversation = await conversations.get(conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(str(conversation_id))
            current = await repositories.runs.current(conversation_id)
            if current is not None:
                await repositories.messages.cancel_generating(current.id)
                await repositories.runs.set_status(current, "cancelled")
            await conversations.end(conversation, reason)
            await session.commit()

    async def delete_conversation(self, conversation_id: int) -> None:
        """Delete one conversation and its checkpoint thread."""
        async with database_module.db.session() as session:
            deleted = await self._repositories.create(session).conversations.delete(
                conversation_id
            )
            await session.commit()
        if deleted:
            await self._runner.delete_thread(conversation_id)

    async def delete_subject(self, adapter: str, subject: JsonObject) -> int:
        """Delete all conversations bound to an opaque business subject."""
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
