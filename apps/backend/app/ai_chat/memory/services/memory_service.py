"""历史 Prompt 选择、按 Run 压缩与压缩追赶。"""

from __future__ import annotations

import json

from app.ai_chat.memory.errors import MemoryCompactionError, MemoryContextFullError
from app.ai_chat.memory.operations import apply_operations
from app.ai_chat.memory.services.memory_persistence_service import (
    MemoryPersistenceService,
)
from app.ai_chat.memory.runs import Memory, Run
from app.ai_chat.memory.settings import memory_settings
from app.ai_chat.memory.summarizer import MemorySummarizer
from app.ai_chat.memory.token_budget import (
    MemoryTokenBudget,
    build_memory_token_budget,
    count_text_tokens,
)


def _history_prompt(memory: Memory,runs: list[Run],) -> str:
    """生成对话模型感知历史 Run 的唯一字符串。"""
    return json.dumps(
        {
            "memory": memory.content_json(),
            "runs": [run.history_record() for run in runs],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _memory_token_count(memory: Memory) -> int:
    """计算累计记忆内容的 Token 数。"""
    spec = build_memory_token_budget()
    payload = json.dumps(
        memory.content_json(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return count_text_tokens(spec, payload)


def _validate_memory_budget(memory: Memory) -> int:
    """限制 Memory 和 other，防止摘要退化成完整 Transcript。"""
    if len(memory.other) > memory_settings.ai_chat_memory_other_max_keys:
        raise MemoryContextFullError("memory_other_keys_full")
    spec = build_memory_token_budget()
    other_total = 0
    for key, value in memory.other.items():
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
    total = _memory_token_count(memory)
    if total > memory_settings.ai_chat_memory_token_cap:
        raise MemoryContextFullError("memory_full")
    return total


class MemoryService:
    """对外只提供历史 Prompt、压缩触发和字符串 Token 计数。"""
    """ runId 一律代表会话的最后一次 run """

    def __init__(
        self,
        summarizer: MemorySummarizer | None = None,
        persistence: MemoryPersistenceService | None = None,
    ) -> None:
        """组装摘要与持久化协作者。"""
        self._summarizer = summarizer or MemorySummarizer()
        self._persistence_service = persistence or MemoryPersistenceService()

    def count_tokens(self, text: str) -> int:
        """使用当前主模型 Tokenizer 计算字符串 Token 数。"""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        spec = build_memory_token_budget()
        return count_text_tokens(spec, text)

    async def get_history_prompt(self, run_id: int, occupied_token: int,) -> str:
        """按 Token 直接确定短期窗口，仅在必要摘要缺失时追赶。"""
        budget = build_memory_token_budget()

        runs = await self._persistence_service.load_history(run_id)
        self._validate_occupied_token(occupied_token, budget)
        short_term_start = self._short_term_start_index(
            runs,
            occupied_token=occupied_token,
            budget=budget,
        )
        prompt = self._prompt_for_boundary(
            runs,
            short_term_start=short_term_start,
            occupied_token=occupied_token,
            budget=budget,
        )
        if prompt is not None:
            return prompt

        # 组装失败时，压缩一次，再尝试组装
        await self.compress(run_id, occupied_token)

        runs = await self._persistence_service.load_history(run_id)
        short_term_start = self._short_term_start_index(
            runs,
            occupied_token=occupied_token,
            budget=budget,
        )
        prompt = self._prompt_for_boundary(
            runs,
            short_term_start=short_term_start,
            occupied_token=occupied_token,
            budget=budget,
        )
        if prompt is None:
            raise MemoryCompactionError("required history run is not compressed")
        return prompt

    def _short_term_start_index(
        self,
        runs: list[Run],
        *,
        occupied_token: int,
        budget: MemoryTokenBudget,
    ) -> int:
        """从最新 Run 反向累计，一次算出短期窗口的起始下标。"""
        empty_memory = Memory()
        raw_prompt = _history_prompt(empty_memory, runs)
        if self._fits(raw_prompt, occupied_token, budget):
            return 0

        document_payload = json.dumps(
            empty_memory.content_json(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        envelope_tokens = max(
            0,
            self.count_tokens(_history_prompt(empty_memory, []))
            - self.count_tokens(document_payload),
        )
        recent_budget = (budget.input_budget - occupied_token - memory_settings.ai_chat_memory_token_cap - envelope_tokens)
        if recent_budget < 0:
            raise MemoryContextFullError("history_context_full")

        used_tokens = 0
        start_index = len(runs)
        for index in range(len(runs) - 1, -1, -1):
            run_payload = json.dumps(
                runs[index].history_record(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            run_tokens = self.count_tokens(run_payload)
            if used_tokens + run_tokens > recent_budget:
                break
            used_tokens += run_tokens
            start_index = index
        return start_index

    def _prompt_for_boundary(
        self,
        runs: list[Run],
        *,
        short_term_start: int,
        occupied_token: int,
        budget: MemoryTokenBudget,
    ) -> str | None:
        """用指定边界组装一次 Prompt；必要摘要不存在时返回 None。"""
        memory = Memory()
        if short_term_start > 0:
            stored_memory = runs[short_term_start - 1].memory
            if stored_memory is None:
                return None
            memory = stored_memory
        prompt = _history_prompt(memory, runs[short_term_start:])
        if not self._fits(prompt, occupied_token, budget):
            raise MemoryContextFullError("history_context_full")
        return prompt

    async def compress(self, run_id: int, occupied_token: int) -> None:
        """压缩窗口外 Run，并继续预压缩短期窗口内最老的两个 Run。"""
        runs = await self._persistence_service.load_history(run_id)
        budget = build_memory_token_budget()
        self._validate_occupied_token(occupied_token, budget)
        if not runs:
            return
        short_term_start = self._short_term_start_index(
            runs,
            occupied_token=occupied_token,
            budget=budget,
        )
        target_index = min(len(runs) - 1, short_term_start + 1)
        await self._ensure_compressed_through(
            runs=runs,
            target_index=target_index,
        )

    def _fits(self, prompt: str, occupied_token: int, budget: MemoryTokenBudget,) -> bool:
        """判断历史 Prompt 是否仍在输入预算内。"""
        return occupied_token + self.count_tokens(prompt) <= budget.input_budget

    @staticmethod
    def _validate_occupied_token(occupied_token: int, budget: MemoryTokenBudget, ) -> None:
        """校验外部已占用 Token 数。"""
        if not isinstance(occupied_token, int):
            raise TypeError("occupied_token must be an integer")
        if occupied_token < 0:
            raise ValueError("occupied_token cannot be negative")
        if occupied_token >= budget.input_budget:
            raise MemoryContextFullError("occupied_context_full")

    async def _ensure_compressed_through(
        self,
        *,
        runs: list[Run],
        target_index: int,
    ) -> None:
        """依次保证目标下标之前的 Run 已压缩。"""
        parent_memory = Memory()
        for run in runs[: target_index + 1]:
            stored = await self._ensure_one(
                run=run,
                parent_memory=parent_memory,
            )
            parent_memory = stored

    async def _ensure_one(
        self,
        *,
        run: Run,
        parent_memory: Memory,
    ) -> Memory:
        """生成或复用单个 Run 的累计记忆。"""
        if run.memory is not None:
            return run.memory
        slot = await self._persistence_service.prepare_compression(
            origin_run=run.origin,
        )
        if slot.completed is not None:
            return slot.completed
        memory_id = slot.memory_id

        try:
            operations = await self._summarizer.summarize(
                parent_memory,
                run.origin,
            )
            memory = apply_operations(
                parent_memory,
                operations,
                run_id=run.run_id,
            )
            token_count = _validate_memory_budget(memory)
            memory = Memory.model_validate(
                {**memory.model_dump(mode="json"), "token_count": token_count}
            )
            completed = await self._persistence_service.complete(
                memory_id=memory_id,
                memory=memory,
            )
            if completed is None:
                raise MemoryCompactionError("completed memory disappeared")
            return completed
        except Exception as exc:
            await self._persistence_service.fail(
                memory_id=memory_id,
                error=str(exc),
            )
            if isinstance(
                exc,
                (MemoryCompactionError, MemoryContextFullError),
            ):
                raise
            raise MemoryCompactionError(
                "memory operations or token accounting are invalid"
            ) from exc
