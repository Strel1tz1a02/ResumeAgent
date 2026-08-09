"""Token 硬预算、完整 Run 裁剪、Memory 追赶与最终上下文组装。"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from app import database as database_module
from app.ai_chat.errors import ContextFullError
from app.ai_chat.graph.runner import GraphRunner
from app.ai_chat.graph.state import AdapterInput, BaseState
from app.ai_chat.memory.operations import MemoryDocument
from app.ai_chat.memory.run_bundles import RunBundle, RunBundleBuilder
from app.ai_chat.memory.service import MemoryService, snapshot_document
from app.ai_chat.model_request import count_request_tokens
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.streaming.events import AiChatEvent
from app.ai_chat.tools.results import PendingToolResult
from app.ai_chat.types import JsonObject
from app.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedContext:
    """已通过最终硬校验、可直接交给 Graph 的输入。"""

    adapter_name: str
    value: AdapterInput
    state: BaseState
    used_tokens: int
    budget_tokens: int
    recent_run_ids: tuple[int, ...]
    short_term_boundary_sequence: int


def _state_messages(state: BaseState) -> list[JsonObject]:
    messages = state.get("model_messages")  # type: ignore[typeddict-item]
    if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
        raise TypeError("adapter state must expose model_messages")
    return messages


def _memory_history(document: MemoryDocument) -> list[JsonObject]:
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


class ContextPlanner:
    """共享 AI Chat 层唯一有权选择历史并校验最终请求的组件。"""

    def __init__(
        self,
        runner: GraphRunner,
        repositories: RepositoryFactory,
        memory: MemoryService,
    ) -> None:
        self._runner = runner
        self._repositories = repositories
        self._memory = memory

    async def prepare(
        self,
        *,
        conversation_id: int,
        run_id: int,
        kind: str,
        user_content: str | None,
        tools_enabled: bool,
    ):
        """流式报告追赶进度，最后产出一个 PreparedContext。"""
        async with database_module.db.session() as session:
            repositories = self._repositories.create(session)
            conversation = await repositories.conversations.get(conversation_id)
            if conversation is None:
                raise LookupError(f"conversation {conversation_id} disappeared")
            bundles = await RunBundleBuilder(session).list_completed(conversation_id)
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
            adapter_name = conversation.adapter
            subject = dict(conversation.subject)
            scope = dict(conversation.scope)
            language = conversation.language

        active = await self._memory.active(conversation_id)
        spec = self._runner.prepare_request(
            adapter_name=adapter_name,
            tools_enabled=tools_enabled and kind != "opening",
        )
        current_history: list[JsonObject] = []
        if user_content is not None:
            current_history.append({"role": "user", "content": user_content})

        async def build_state(history: list[JsonObject]) -> tuple[AdapterInput, BaseState, int]:
            value: AdapterInput = {
                "conversation_id": conversation_id,
                "run_id": run_id,
                "subject": subject,
                "scope": scope,
                "language": language,
                "run_kind": kind,
                "tools_enabled": tools_enabled,
                "messages": history,
                "pending_tool_results": pending,
                "model_request": spec.model_dump(mode="json"),
            }
            state = await self._runner.prepare_state(
                adapter_name=adapter_name, value=value
            )
            used = count_request_tokens(spec, _state_messages(state))
            return value, state, used

        _, _, fixed_tokens = await build_state(current_history)
        recent_budget = spec.input_budget - fixed_tokens - settings.ai_chat_memory_token_cap
        if recent_budget < 0:
            raise ContextFullError(
                "fixed_input_too_large",
                used_tokens=fixed_tokens,
                budget_tokens=spec.input_budget,
            )
        eligible = [
            bundle
            for bundle in bundles
            if bundle.last_sequence > active.covered_through_sequence
        ]
        selected_reversed: list[RunBundle] = []
        selected_tokens = 0
        for bundle in reversed(eligible):
            bundle_tokens = count_request_tokens(spec, bundle.model_messages())
            if selected_tokens + bundle_tokens > recent_budget:
                break
            selected_reversed.append(bundle)
            selected_tokens += bundle_tokens
        recent = list(reversed(selected_reversed))

        # 单块 Token 相加只用于快速选取；真正的 Chat 包装开销不是严格可加。
        # 在任何摘要/晋升之前，先用最终 Adapter Renderer 把边界收紧。
        while recent:
            provisional_history = _memory_history(snapshot_document(active))
            for bundle in recent:
                provisional_history.extend(bundle.model_messages())
            provisional_history.extend(current_history)
            _, _, provisional_used = await build_state(provisional_history)
            if provisional_used <= spec.input_budget:
                break
            recent = recent[1:]

        compaction_started = False
        while True:
            excluded_count = len(eligible) - len(recent)
            promotion = eligible[excluded_count - 1] if excluded_count else None
            if (
                promotion is not None
                and promotion.last_sequence > active.covered_through_sequence
            ):
                promotion_index = bundles.index(promotion)
                precompression = bundles[
                    min(len(bundles) - 1, promotion_index + 2)
                ]
                promotion_covered = await self._memory.covers(
                    conversation_id, promotion.run_id, bundles=bundles
                )
                precompression_covered = await self._memory.covers(
                    conversation_id, precompression.run_id, bundles=bundles
                )
                if (not promotion_covered) or (
                    compaction_started and not precompression_covered
                ):
                    if not compaction_started:
                        compaction_started = True
                        yield AiChatEvent("memory.compaction.started", {})
                    queue: asyncio.Queue[tuple[int, int]] = asyncio.Queue()

                    async def report(done: int, total: int) -> None:
                        await queue.put((done, total))

                    task = asyncio.create_task(
                        self._memory.ensure_chain(
                            conversation_id=conversation_id,
                            bundles=bundles,
                            target_run_id=precompression.run_id,
                            progress=report,
                        )
                    )
                    try:
                        while not task.done():
                            try:
                                done, total = await asyncio.wait_for(queue.get(), 0.1)
                            except TimeoutError:
                                continue
                            yield AiChatEvent(
                                "memory.compaction.progress",
                                {"completed_runs": done, "target_runs": total},
                            )
                        await task
                        while not queue.empty():
                            done, total = queue.get_nowait()
                            yield AiChatEvent(
                                "memory.compaction.progress",
                                {"completed_runs": done, "target_runs": total},
                            )
                    finally:
                        if not task.done():
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                active = await self._memory.promote(
                    conversation_id=conversation_id,
                    target_run_id=promotion.run_id,
                    bundles=bundles,
                )

            document = snapshot_document(active)
            history = _memory_history(document)
            for bundle in recent:
                history.extend(bundle.model_messages())
            history.extend(current_history)
            value, state, used = await build_state(history)
            if used <= spec.input_budget:
                boundary = active.covered_through_sequence
                logger.info(
                    "AI Chat context prepared: conversation=%s run=%s used=%s "
                    "budget=%s recent_runs=%s active_sequence=%s memory_tokens=%s",
                    conversation_id,
                    run_id,
                    used,
                    spec.input_budget,
                    len(recent),
                    boundary,
                    active.memory_token_count,
                )
                if compaction_started:
                    yield AiChatEvent("memory.compaction.completed", {})
                yield PreparedContext(
                    adapter_name=adapter_name,
                    value=value,
                    state=state,
                    used_tokens=used,
                    budget_tokens=spec.input_budget,
                    recent_run_ids=tuple(bundle.run_id for bundle in recent),
                    short_term_boundary_sequence=boundary,
                )
                return
            if not recent:
                raise ContextFullError(
                    "fixed_input_too_large",
                    used_tokens=used,
                    budget_tokens=spec.input_budget,
                )
            recent = recent[1:]
