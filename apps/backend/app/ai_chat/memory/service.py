"""Snapshot Chain 的预压缩、校验与晋升服务。"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app import database as database_module
from app.ai_chat.errors import ContextFullError, MemoryCompactionError
from app.ai_chat.memory.operations import MemoryDocument, apply_operations
from app.ai_chat.memory.run_bundles import RunBundle, RunBundleBuilder
from app.ai_chat.memory.summarizer import MemorySummarizer
from app.ai_chat.model_request import build_model_request_spec, count_request_tokens
from app.ai_chat.models import AiChatConversationMemorySnapshot
from app.ai_chat.repositories import RepositoryFactory
from app.config import settings
from app.llm import get_llm_config

Progress = Callable[[int, int], Awaitable[None]]
logger = logging.getLogger(__name__)


def snapshot_document(snapshot: AiChatConversationMemorySnapshot) -> MemoryDocument:
    return MemoryDocument.model_validate({**dict(snapshot.core), "other": dict(snapshot.other)})


def memory_token_count(document: MemoryDocument) -> int:
    spec = build_model_request_spec({}, tools_enabled=False)
    payload = json.dumps(document.model_dump(mode="json"), ensure_ascii=False)
    return count_request_tokens(spec, [{"role": "user", "content": payload}])


def validate_memory_budget(document: MemoryDocument) -> int:
    """强制整体与 Other 的分层上限，避免 Other 退化成 Transcript。"""
    if len(document.other) > settings.ai_chat_memory_other_max_keys:
        raise ContextFullError("memory_other_keys_full")
    spec = build_model_request_spec({}, tools_enabled=False)
    other_total = 0
    for key, value in document.other.items():
        payload = json.dumps({key: value}, ensure_ascii=False)
        tokens = count_request_tokens(spec, [{"role": "user", "content": payload}])
        if tokens > settings.ai_chat_memory_other_field_token_cap:
            raise ContextFullError("memory_other_field_full")
        other_total += tokens
    if other_total > settings.ai_chat_memory_other_token_cap:
        raise ContextFullError("memory_other_full")
    total = memory_token_count(document)
    if total > settings.ai_chat_memory_token_cap:
        raise ContextFullError("memory_full")
    return total


class MemoryService:
    """前后台共享同一条 ensure_chain 实现。"""

    def __init__(
        self,
        repositories: RepositoryFactory,
        summarizer: MemorySummarizer | None = None,
    ) -> None:
        self._repositories = repositories
        self._summarizer = summarizer or MemorySummarizer()

    async def ensure_root(self, conversation_id: int) -> None:
        async with database_module.db.session() as session:
            await self._repositories.create(session).memory.get_or_create(conversation_id)
            await session.commit()

    async def active(
        self, conversation_id: int
    ) -> AiChatConversationMemorySnapshot:
        async with database_module.db.session() as session:
            _, active = await self._repositories.create(session).memory.get_or_create(
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
            memory = self._repositories.create(session).memory
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
                memory = self._repositories.create(session).memory
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
                await self._repositories.create(session).memory.release_lease(
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
            memory = self._repositories.create(session).memory
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
            except ContextFullError:
                raise
            except Exception as exc:
                raise MemoryCompactionError(
                    "memory operations or token accounting are invalid"
                ) from exc
            async with database_module.db.session() as session:
                memory = self._repositories.create(session).memory
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
            memory = self._repositories.create(session).memory
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


class MemoryMaintainer:
    """Run 完成后异步维持 Active 后两个 Staged Snapshot。"""

    def __init__(self, memory: MemoryService) -> None:
        self._memory = memory
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def schedule(self, conversation_id: int) -> None:
        try:
            config = get_llm_config()
        except Exception:
            logger.exception("Cannot resolve LLM config for background memory compaction")
            return
        if not config.api_key and config.provider not in {"ollama", "openai_compatible"}:
            logger.info(
                "Skip AI Chat background memory compaction without an available provider"
            )
            return
        current = self._tasks.get(conversation_id)
        if current is not None and not current.done():
            return
        task = asyncio.create_task(self._maintain(conversation_id))
        self._tasks[conversation_id] = task

        def finished(done: asyncio.Task[None]) -> None:
            self._tasks.pop(conversation_id, None)
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    "AI Chat background memory compaction failed: conversation=%s",
                    conversation_id,
                )

        task.add_done_callback(finished)

    async def close(self) -> None:
        tasks = tuple(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _maintain(self, conversation_id: int) -> None:
        while True:
            async with database_module.db.session() as session:
                bundles = await RunBundleBuilder(session).list_completed(conversation_id)
            active = await self._memory.active(conversation_id)
            candidates = [
                bundle
                for bundle in bundles
                if bundle.last_sequence > active.covered_through_sequence
            ][:2]
            if not candidates:
                return
            target = candidates[-1]
            if await self._memory.covers(
                conversation_id, target.run_id, bundles=bundles
            ):
                return
            await self._memory.ensure_chain(
                conversation_id=conversation_id,
                bundles=bundles,
                target_run_id=target.run_id,
            )
