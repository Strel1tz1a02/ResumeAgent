"""Snapshot Chain 的预压缩、校验与晋升服务。"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app import database as database_module
from app.ai_chat.adapters import AdapterRegistry
from app.ai_chat.graph.state import AdapterInput
from app.ai_chat.memory.errors import MemoryCompactionError, MemoryContextFullError
from app.ai_chat.memory.models import AiChatConversationMemorySnapshot
from app.ai_chat.memory.operations import MemoryDocument, apply_operations
from app.ai_chat.memory.repository import MemoryRepository
from app.ai_chat.memory.run_bundles import RunBundle, RunBundleBuilder
from app.ai_chat.memory.settings import memory_settings
from app.ai_chat.memory.summarizer import MemorySummarizer
from app.ai_chat.memory.token_budget import (
    MemoryTokenBudget,
    build_memory_token_budget,
    count_request_tokens,
)
from app.ai_chat.models import AiChatMessage, AiChatRun
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.types import JsonObject

Progress = Callable[[int, int], Awaitable[None]]
logger = logging.getLogger(__name__)


def snapshot_document(snapshot: AiChatConversationMemorySnapshot) -> MemoryDocument:
    return MemoryDocument.model_validate({**dict(snapshot.core), "other": dict(snapshot.other)})


def memory_token_count(document: MemoryDocument) -> int:
    spec = build_memory_token_budget({}, tools_enabled=False)
    payload = json.dumps(document.model_dump(mode="json"), ensure_ascii=False)
    return count_request_tokens(spec, [{"role": "user", "content": payload}])


def validate_memory_budget(document: MemoryDocument) -> int:
    """强制整体与 Other 的分层上限，避免 Other 退化成 Transcript。"""
    if len(document.other) > memory_settings.ai_chat_memory_other_max_keys:
        raise MemoryContextFullError("memory_other_keys_full")
    spec = build_memory_token_budget({}, tools_enabled=False)
    other_total = 0
    for key, value in document.other.items():
        payload = json.dumps({key: value}, ensure_ascii=False)
        tokens = count_request_tokens(spec, [{"role": "user", "content": payload}])
        if tokens > memory_settings.ai_chat_memory_other_field_token_cap:
            raise MemoryContextFullError("memory_other_field_full")
        other_total += tokens
    if other_total > memory_settings.ai_chat_memory_other_token_cap:
        raise MemoryContextFullError("memory_other_full")
    total = memory_token_count(document)
    if total > memory_settings.ai_chat_memory_token_cap:
        raise MemoryContextFullError("memory_full")
    return total


class _SnapshotService:
    """Memory facade 内部共享的 Snapshot Chain 实现。"""

    def __init__(
        self,
        summarizer: MemorySummarizer | None = None,
    ) -> None:
        self._summarizer = summarizer or MemorySummarizer()

    async def ensure_root(self, conversation_id: int) -> None:
        async with database_module.db.session() as session:
            await MemoryRepository(session).get_or_create(conversation_id)
            await session.commit()

    async def active(
        self, conversation_id: int
    ) -> AiChatConversationMemorySnapshot:
        async with database_module.db.session() as session:
            _, active = await MemoryRepository(session).get_or_create(
                conversation_id
            )
            await session.commit()
            session.expunge(active)
            return active

    async def covers(
        self,
        conversation_id: int,
        target_run_id: int,
        *,
        bundles: list[RunBundle] | None = None,
    ) -> bool:
        """判断规范链是否覆盖目标，并可顺便清除来源已变化的 Stage。"""
        async with database_module.db.session() as session:
            memory = MemoryRepository(session)
            _, active = await memory.get_or_create(conversation_id)
            if active.source_run_id == target_run_id:
                await session.commit()
                return True
            chain = await memory.chain_from(active.id)
            if bundles is not None:
                bundle_by_run = {bundle.run_id: bundle for bundle in bundles}
                for child in chain:
                    source = bundle_by_run.get(child.source_run_id or -1)
                    if (
                        source is None
                        or child.source_bundle_hash != source.stable_hash()
                        or child.covered_through_sequence != source.last_sequence
                    ):
                        await session.execute(
                            delete(AiChatConversationMemorySnapshot).where(
                                AiChatConversationMemorySnapshot.id == child.id
                            )
                        )
                        await session.commit()
                        return False
            await session.commit()
            return any(child.source_run_id == target_run_id for child in chain)

    async def ensure_chain(
        self,
        *,
        conversation_id: int,
        bundles: list[RunBundle],
        target_run_id: int,
        progress: Progress | None = None,
    ) -> AiChatConversationMemorySnapshot:
        """保证规范链连续覆盖到 target；LLM 调用期间不持有事务。"""
        positions = {bundle.run_id: index for index, bundle in enumerate(bundles)}
        if target_run_id not in positions:
            raise MemoryCompactionError("compaction target is not a completed RunBundle")
        owner = uuid.uuid4().hex
        acquired = False
        for _ in range(200):
            async with database_module.db.session() as session:
                memory = MemoryRepository(session)
                await memory.get_or_create(conversation_id)
                acquired = await memory.acquire_lease(conversation_id, owner)
                await session.commit()
            if acquired:
                break
            await asyncio.sleep(0.05)
        if not acquired:
            raise MemoryCompactionError("memory compaction lease is busy")
        try:
            return await self._ensure_owned_chain(
                conversation_id=conversation_id,
                bundles=bundles,
                target_index=positions[target_run_id],
                progress=progress,
            )
        finally:
            async with database_module.db.session() as session:
                await MemoryRepository(session).release_lease(
                    conversation_id, owner
                )
                await session.commit()

    async def _ensure_owned_chain(
        self,
        *,
        conversation_id: int,
        bundles: list[RunBundle],
        target_index: int,
        progress: Progress | None,
    ) -> AiChatConversationMemorySnapshot:
        async with database_module.db.session() as session:
            memory = MemoryRepository(session)
            _, active = await memory.get_or_create(conversation_id)
            chain = await memory.chain_from(active.id)
            valid_tail = active
            start_index = next(
                (
                    index + 1
                    for index, bundle in enumerate(bundles)
                    if bundle.last_sequence == active.covered_through_sequence
                ),
                0,
            )
            base_index = start_index
            valid_count = 0
            for offset, child in enumerate(chain):
                bundle_index = base_index + offset
                if bundle_index >= len(bundles):
                    await session.execute(
                        delete(AiChatConversationMemorySnapshot).where(
                            AiChatConversationMemorySnapshot.id == child.id
                        )
                    )
                    break
                bundle = bundles[bundle_index]
                if (
                    child.source_run_id != bundle.run_id
                    or child.source_bundle_hash != bundle.stable_hash()
                    or child.covered_through_sequence != bundle.last_sequence
                ):
                    await session.execute(
                        delete(AiChatConversationMemorySnapshot).where(
                            AiChatConversationMemorySnapshot.id == child.id
                        )
                    )
                    break
                valid_tail = child
                valid_count += 1
            start_index = base_index + valid_count
            await session.commit()
            session.expunge(valid_tail)

        missing = max(0, target_index - start_index + 1)
        completed = 0
        parent = valid_tail
        for bundle_index in range(start_index, target_index + 1):
            bundle = bundles[bundle_index]
            parent_doc = snapshot_document(parent)
            operations = await self._summarizer.summarize(parent_doc, bundle)
            try:
                candidate = apply_operations(parent_doc, operations)
                tokens = validate_memory_budget(candidate)
            except MemoryContextFullError:
                raise
            except Exception as exc:
                raise MemoryCompactionError(
                    "memory operations or token accounting are invalid"
                ) from exc
            async with database_module.db.session() as session:
                memory = MemoryRepository(session)
                fresh_bundles = await RunBundleBuilder(session).list_completed(
                    conversation_id
                )
                fresh_bundle = next(
                    (item for item in fresh_bundles if item.run_id == bundle.run_id),
                    None,
                )
                if (
                    fresh_bundle is None
                    or fresh_bundle.stable_hash() != bundle.stable_hash()
                ):
                    raise MemoryCompactionError(
                        "RunBundle changed during memory compaction"
                    )
                durable_parent = await memory.snapshot(parent.id)
                if durable_parent is None:
                    raise MemoryCompactionError("snapshot parent changed during compaction")
                try:
                    child = await memory.create_child(
                        parent=durable_parent,
                        bundle=bundle,
                        operations=operations,
                        document=candidate,
                        memory_token_count=tokens,
                    )
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    chain = await memory.chain_from(parent.id)
                    if not chain or chain[0].source_bundle_hash != bundle.stable_hash():
                        raise MemoryCompactionError("snapshot chain forked")
                    child = chain[0]
                session.expunge(child)
                parent = child
            completed += 1
            if progress is not None:
                await progress(completed, missing)
        return parent

    async def promote(
        self,
        *,
        conversation_id: int,
        target_run_id: int,
        bundles: list[RunBundle],
    ) -> AiChatConversationMemorySnapshot:
        """重验来源 Hash 后，无 LLM 地把目标节点晋升为 Active。"""
        bundle_by_run = {bundle.run_id: bundle for bundle in bundles}
        async with database_module.db.session() as session:
            memory = MemoryRepository(session)
            pointer, active = await memory.get_or_create(conversation_id)
            chain = await memory.chain_from(active.id)
            target: AiChatConversationMemorySnapshot | None = None
            for child in chain:
                bundle = bundle_by_run.get(child.source_run_id or -1)
                if bundle is None or child.source_bundle_hash != bundle.stable_hash():
                    await session.execute(
                        delete(AiChatConversationMemorySnapshot).where(
                            AiChatConversationMemorySnapshot.id == child.id
                        )
                    )
                    await session.commit()
                    raise MemoryCompactionError("staged snapshot source changed")
                if child.source_run_id == target_run_id:
                    target = child
                    break
            if target is None:
                raise MemoryCompactionError("promotion target is missing")
            if not await memory.promote(
                conversation_id=conversation_id,
                expected_active_id=pointer.active_snapshot_id,
                target=target,
            ):
                raise MemoryCompactionError("active pointer changed during promotion")
            await memory.delete_descendants_after(target.id, keep=2)
            await session.commit()
            session.expunge(target)
            return target


def _memory_message(document: MemoryDocument) -> list[JsonObject]:
    """把派生记忆明确标记为非权威历史，而不是系统指令。"""
    if document == MemoryDocument():
        return []
    return [
        {
            "role": "user",
            "content": "CONVERSATION_MEMORY_DERIVED_NON_AUTHORITATIVE\n"
            + json.dumps(document.model_dump(mode="json"), ensure_ascii=False)
            + "\nEND_CONVERSATION_MEMORY",
        }
    ]


def _rendered_messages(state: Mapping[str, object]) -> list[JsonObject]:
    messages = state.get("model_messages")
    if not isinstance(messages, list) or not all(
        isinstance(message, dict) for message in messages
    ):
        raise TypeError("adapter state must expose model_messages")
    return [dict(message) for message in messages]


class MemoryContextService:
    """Memory 模块唯一公开入口：返回本轮应拼入的历史 Messages。"""

    def __init__(
        self,
        registry: AdapterRegistry,
        repositories: RepositoryFactory,
        summarizer: MemorySummarizer | None = None,
    ) -> None:
        self._registry = registry
        self._repositories = repositories
        self._snapshots = _SnapshotService(summarizer)

    async def get_context_messages(
        self,
        *,
        conversation_id: int,
        run_id: int,
        run_kind: str,
        tools_enabled: bool,
    ) -> list[JsonObject]:
        """返回 Active Memory、完整短期 Runs 和当前 Run 的已完成消息。"""
        async with database_module.db.session() as session:
            repositories = self._repositories.create(session)
            conversation = await repositories.conversations.get(conversation_id)
            if conversation is None:
                raise LookupError(f"conversation {conversation_id} disappeared")
            run = await session.get(AiChatRun, run_id)
            if run is None or run.conversation_id != conversation_id:
                raise ValueError("run does not belong to conversation")
            bundles = await RunBundleBuilder(session).list_completed(conversation_id)
            current_result = await session.execute(
                select(AiChatMessage)
                .where(
                    AiChatMessage.conversation_id == conversation_id,
                    AiChatMessage.run_id == run_id,
                    AiChatMessage.status == "completed",
                    AiChatMessage.role.in_(("user", "assistant")),
                )
                .order_by(AiChatMessage.sequence)
            )
            current_messages = [
                {"role": row.role, "content": row.content}
                for row in current_result.scalars().all()
            ]
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
            adapter_name = conversation.adapter
            subject = dict(conversation.subject)
            scope = dict(conversation.scope)
            language = conversation.language

        adapter = self._registry.get(adapter_name)
        budget = build_memory_token_budget(
            adapter.get_tool_handlers(),
            tools_enabled=tools_enabled and run_kind != "opening",
        )

        async def count_history(history: list[JsonObject]) -> int:
            value: AdapterInput = {
                "conversation_id": conversation_id,
                "run_id": run_id,
                "subject": subject,
                "scope": scope,
                "language": language,
                "run_kind": run_kind,
                "tools_enabled": tools_enabled,
                "messages": history,
                "pending_tool_results": pending,
            }
            state = await adapter.parse_input(value)
            return count_request_tokens(budget, _rendered_messages(state))

        return await self._select_messages(
            conversation_id=conversation_id,
            bundles=bundles,
            current_messages=current_messages,
            budget=budget,
            count_history=count_history,
        )

    async def _select_messages(
        self,
        *,
        conversation_id: int,
        bundles: list[RunBundle],
        current_messages: list[JsonObject],
        budget: MemoryTokenBudget,
        count_history: Callable[[list[JsonObject]], Awaitable[int]],
    ) -> list[JsonObject]:
        active = await self._snapshots.active(conversation_id)
        fixed_tokens = await count_history(current_messages)
        if fixed_tokens > budget.input_budget:
            raise MemoryContextFullError("fixed_input_too_large")

        eligible = [
            bundle
            for bundle in bundles
            if bundle.last_sequence > active.covered_through_sequence
        ]
        recent: list[RunBundle] = []
        for bundle in reversed(eligible):
            candidate = [bundle, *recent]
            candidate_history = _memory_message(snapshot_document(active))
            for candidate_bundle in candidate:
                candidate_history.extend(candidate_bundle.model_messages())
            candidate_history.extend(current_messages)
            if await count_history(candidate_history) > budget.input_budget:
                break
            recent = candidate

        while True:
            excluded_count = len(eligible) - len(recent)
            if excluded_count:
                promotion = eligible[excluded_count - 1]
                promotion_index = bundles.index(promotion)
                buffer_target = bundles[
                    min(len(bundles) - 1, promotion_index + 2)
                ]
                await self._snapshots.ensure_chain(
                    conversation_id=conversation_id,
                    bundles=bundles,
                    target_run_id=buffer_target.run_id,
                )
                active = await self._snapshots.promote(
                    conversation_id=conversation_id,
                    target_run_id=promotion.run_id,
                    bundles=bundles,
                )

            history = _memory_message(snapshot_document(active))
            for bundle in recent:
                history.extend(bundle.model_messages())
            history.extend(current_messages)
            used_tokens = await count_history(history)
            if used_tokens <= budget.input_budget:
                logger.info(
                    "AI Chat memory context selected: conversation=%s used=%s "
                    "budget=%s recent_runs=%s active_sequence=%s",
                    conversation_id,
                    used_tokens,
                    budget.input_budget,
                    len(recent),
                    active.covered_through_sequence,
                )
                return history
            if not recent:
                raise MemoryContextFullError("fixed_input_too_large")
            recent = recent[1:]
