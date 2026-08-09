"""历史 Prompt 选择、按 Run 压缩与压缩追赶。"""

from __future__ import annotations

import json

from app import database as database_module
from app.ai_chat.memory.errors import MemoryCompactionError, MemoryContextFullError
from app.ai_chat.memory.operations import MemoryDocument, apply_operations
from app.ai_chat.memory.run_bundles import RunBundle, RunBundleBuilder
from app.ai_chat.memory.settings import memory_settings
from app.ai_chat.memory.summarizer import MemorySummarizer
from app.ai_chat.memory.token_budget import (
    MemoryTokenBudget,
    build_memory_token_budget,
    count_text_tokens,
)
from app.ai_chat.models import AiChatRunMemory
from app.ai_chat.repositories.memory_repository import MemoryRepository

def _memory_document(row: AiChatRunMemory) -> MemoryDocument:
    return MemoryDocument.model_validate(
        {**dict(row.core), "other": dict(row.other)}
    )


def _history_prompt(
    document: MemoryDocument,
    bundles: list[RunBundle],
) -> str:
    """生成对话模型感知历史 Run 的唯一字符串。"""
    return json.dumps(
        {
            "memory": document.model_dump(mode="json"),
            "runs": [bundle.history_record() for bundle in bundles],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _memory_token_count(document: MemoryDocument) -> int:
    spec = build_memory_token_budget({}, tools_enabled=False)
    payload = json.dumps(
        document.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return count_text_tokens(spec, payload)


def _validate_memory_budget(document: MemoryDocument) -> int:
    """限制 Memory 和 other，防止摘要退化成完整 Transcript。"""
    if len(document.other) > memory_settings.ai_chat_memory_other_max_keys:
        raise MemoryContextFullError("memory_other_keys_full")
    spec = build_memory_token_budget({}, tools_enabled=False)
    other_total = 0
    for key, value in document.other.items():
        payload = json.dumps(
            {key: value},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        tokens = count_text_tokens(spec, payload)
        if tokens > memory_settings.ai_chat_memory_other_field_token_cap:
            raise MemoryContextFullError("memory_other_field_full")
        other_total += tokens
    if other_total > memory_settings.ai_chat_memory_other_token_cap:
        raise MemoryContextFullError("memory_other_full")
    total = _memory_token_count(document)
    if total > memory_settings.ai_chat_memory_token_cap:
        raise MemoryContextFullError("memory_full")
    return total


class MemoryContextService:
    """对外只提供历史 Prompt、压缩触发和字符串 Token 计数。"""
    """ runId 一律代表会话的最后一次 run """

    def __init__(self, summarizer: MemorySummarizer | None = None) -> None:
        self._summarizer = summarizer or MemorySummarizer()

    def count_tokens(self, text: str) -> int:
        """使用当前主模型 Tokenizer 计算字符串 Token 数。"""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        spec = self._token_budget()
        return count_text_tokens(spec, text)

    async def get_history_prompt(self, run_id: int, occupied_token: int,) -> str:
        """等待必要压缩与缓冲追赶完成，再返回目标 Run 之前的历史。"""
        await self.compress(run_id, occupied_token)
        _, bundles = await self._load_history(run_id)
        budget = self._token_budget()
        self._validate_occupied_token(occupied_token, budget)
        document = MemoryDocument()
        parent_memory_id: int | None = None

        for split_index in range(len(bundles) + 1):
            prompt = _history_prompt(document, bundles[split_index:])
            if self._fits(prompt, occupied_token, budget):
                return prompt
            if split_index == len(bundles):
                break
            row = await self._completed_memory(
                bundles[split_index],
                parent_memory_id=parent_memory_id,
            )
            if row is None:
                raise MemoryCompactionError(
                    "required history run is not compressed"
                )
            document = _memory_document(row)
            parent_memory_id = row.id
        raise MemoryContextFullError("history_context_full")

    async def compress(self, run_id: int, occupied_token: int) -> None:
        """压缩窗口外 Run，并继续预压缩短期窗口内最老的两个 Run。"""
        conversation_id, bundles = await self._load_history(run_id)
        budget = self._token_budget()
        self._validate_occupied_token(occupied_token, budget)
        document = MemoryDocument()
        split_index = 0

        while True:
            prompt = _history_prompt(document, bundles[split_index:])
            if self._fits(prompt, occupied_token, budget):
                break
            if split_index >= len(bundles):
                raise MemoryContextFullError("history_context_full")
            await self._ensure_compressed_through(
                boundary_run_id=run_id,
                conversation_id=conversation_id,
                bundles=bundles,
                target_index=split_index,
            )
            row = await self._completed_memory(
                bundles[split_index],
                parent_memory_id=(
                    None
                    if split_index == 0
                    else await self._memory_id_for(bundles[split_index - 1])
                ),
            )
            if row is None:
                raise MemoryCompactionError("compression did not produce a snapshot")
            document = _memory_document(row)
            split_index += 1

        if split_index < len(bundles):
            buffer_target = min(len(bundles) - 1, split_index + 1)
            await self._ensure_compressed_through(
                boundary_run_id=run_id,
                conversation_id=conversation_id,
                bundles=bundles,
                target_index=buffer_target,
            )

    @staticmethod
    def _token_budget() -> MemoryTokenBudget:
        return build_memory_token_budget({}, tools_enabled=False)

    def _fits(
        self,
        prompt: str,
        occupied_token: int,
        budget: MemoryTokenBudget,
    ) -> bool:
        return occupied_token + self.count_tokens(prompt) <= budget.input_budget

    @staticmethod
    def _validate_occupied_token(
        occupied_token: int,
        budget: MemoryTokenBudget,
    ) -> None:
        if isinstance(occupied_token, bool) or not isinstance(occupied_token, int):
            raise TypeError("occupied_token must be an integer")
        if occupied_token < 0:
            raise ValueError("occupied_token cannot be negative")
        if occupied_token >= budget.input_budget:
            raise MemoryContextFullError("occupied_context_full")

    @staticmethod
    async def _load_history(run_id: int) -> tuple[int, list[RunBundle]]:
        async with database_module.db.session() as session:
            boundary, bundles = await RunBundleBuilder(session).history_before(run_id)
            return boundary.conversation_id, bundles

    @staticmethod
    async def _memory_id_for(bundle: RunBundle) -> int | None:
        async with database_module.db.session() as session:
            row = await MemoryRepository(session).get_by_run_id(bundle.run_id)
            if row is None or row.status != "completed":
                return None
            return row.id

    @staticmethod
    async def _completed_memory(
        bundle: RunBundle,
        *,
        parent_memory_id: int | None,
    ) -> AiChatRunMemory | None:
        async with database_module.db.session() as session:
            row = await MemoryRepository(session).get_by_run_id(bundle.run_id)
            if (
                row is None
                or row.status != "completed"
                or row.source_bundle_hash != bundle.stable_hash()
                or row.parent_memory_id != parent_memory_id
            ):
                return None
            session.expunge(row)
            return row

    async def _ensure_compressed_through(
        self,
        *,
        boundary_run_id: int,
        conversation_id: int,
        bundles: list[RunBundle],
        target_index: int,
    ) -> None:
        parent_memory_id: int | None = None
        parent_document = MemoryDocument()
        for bundle in bundles[: target_index + 1]:
            row = await self._ensure_one(
                boundary_run_id=boundary_run_id,
                conversation_id=conversation_id,
                bundle=bundle,
                parent_memory_id=parent_memory_id,
                parent_document=parent_document,
            )
            parent_memory_id = row.id
            parent_document = _memory_document(row)

    async def _ensure_one(
        self,
        *,
        boundary_run_id: int,
        conversation_id: int,
        bundle: RunBundle,
        parent_memory_id: int | None,
        parent_document: MemoryDocument,
    ) -> AiChatRunMemory:
        source_hash = bundle.stable_hash()
        async with database_module.db.session() as session:
            repository = MemoryRepository(session)
            row = await repository.get_by_run_id(bundle.run_id)
            if row is not None and (
                row.conversation_id != conversation_id
                or row.parent_memory_id != parent_memory_id
                or row.source_bundle_hash != source_hash
            ):
                await repository.delete_chain_from(row.id)
                await session.commit()
                row = None
            if row is None:
                row = await repository.create_placeholder(
                    conversation_id=conversation_id,
                    parent_memory_id=parent_memory_id,
                    bundle=bundle,
                )
                await session.commit()
            if row.status == "completed":
                session.expunge(row)
                return row
            memory_id = row.id

        try:
            operations = await self._summarizer.summarize(
                parent_document,
                bundle,
            )
            document = apply_operations(parent_document, operations)
            token_count = _validate_memory_budget(document)
            await self._verify_source(boundary_run_id, bundle)
            async with database_module.db.session() as session:
                repository = MemoryRepository(session)
                completed = await repository.complete(
                    memory_id=memory_id,
                    operations=operations,
                    document=document,
                    memory_token_count=token_count,
                )
                if not completed:
                    raise MemoryCompactionError("memory placeholder disappeared")
                await session.commit()
                row = await repository.get_by_run_id(bundle.run_id)
                if row is None:
                    raise MemoryCompactionError("completed memory disappeared")
                session.expunge(row)
                return row
        except Exception as exc:
            async with database_module.db.session() as session:
                await MemoryRepository(session).fail(
                    memory_id=memory_id,
                    error=str(exc),
                )
                await session.commit()
            if isinstance(
                exc,
                (MemoryCompactionError, MemoryContextFullError),
            ):
                raise
            raise MemoryCompactionError(
                "memory operations or token accounting are invalid"
            ) from exc

    @staticmethod
    async def _verify_source(
        boundary_run_id: int,
        expected: RunBundle,
    ) -> None:
        async with database_module.db.session() as session:
            _, bundles = await RunBundleBuilder(session).history_before(
                boundary_run_id
            )
        fresh = next(
            (bundle for bundle in bundles if bundle.run_id == expected.run_id),
            None,
        )
        if fresh is None or fresh.stable_hash() != expected.stable_hash():
            raise MemoryCompactionError("RunBundle changed during compression")
