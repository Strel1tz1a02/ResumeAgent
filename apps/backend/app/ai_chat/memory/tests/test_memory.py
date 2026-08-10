"""历史 Prompt、终态 Run 和按 Run 压缩状态测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select, text

from app.ai_chat.memory.errors import MemoryCompactionError, MemoryContextFullError
from app.ai_chat.memory.operations import MemoryOperation, apply_operations
from app.ai_chat.memory.runs import (
    Memory,
    OriginRun,
    Run,
)
from app.ai_chat.memory.services.memory_service import MemoryService
from app.ai_chat.memory.services.memory_persistence_service import (
    MemoryPersistenceService,
)
from app.ai_chat.memory.settings import memory_settings
from app.ai_chat.memory.summarizer import MemorySummarizer
from app.ai_chat.memory.token_budget import MemoryTokenBudget
from app.ai_chat.models import AiChatRunMemory
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.repositories.memory_repository import MemoryRepository
from app.ai_chat.repositories.origin_run_repository import OriginRunRepository


def test_operations_enforce_core_and_other_boundaries() -> None:
    parent = Memory(
        current_goal="整理经历",
        preferences=["使用简洁措辞"],
        other={"example_to_follow": "示例 A"},
    )
    result = apply_operations(
        parent,
        [
            MemoryOperation(
                op="update",
                path="core.current_goal",
                value="完善项目经历",
            ),
            MemoryOperation(op="delete", path="other.example_to_follow"),
            MemoryOperation(
                op="add",
                path="other.preferred_wording",
                value="保留口语感",
            ),
        ],
    )
    assert result.current_goal == "完善项目经历"
    assert result.preferences == ["使用简洁措辞"]
    assert result.other == {"preferred_wording": "保留口语感"}

    with pytest.raises(ValueError, match="add can only create other"):
        MemoryOperation(op="add", path="core.constraints", value=["必须真实"])


def test_run_exposes_origin_and_cumulative_memory() -> None:
    origin = OriginRun(
        run_id=7,
        kind="chat",
        status="completed",
        error_code=None,
        messages=({"role": "user", "content": "记住简洁", "status": "completed"},),
        tool_calls=(),
    )
    memory = Memory(
        run_id=7,
        preferences=["简洁"],
        token_count=12,
    )

    run = Run(origin=origin, memory=memory)

    assert run.run_id == 7
    assert run.history_record() == origin.history_record()
    assert run.memory is memory
    assert run.memory.preferences == ["简洁"]

    with pytest.raises(ValueError, match="same run"):
        Run(
            origin=origin,
            memory=Memory(
                run_id=8,
                token_count=0,
            ),
        )


async def test_memory_table_contains_only_persisted_results(isolated_db) -> None:
    async with isolated_db.session() as session:
        rows = (
            await session.execute(text("PRAGMA table_info(ai_chat_run_memories)"))
        ).all()

    columns = {str(row[1]) for row in rows}
    assert columns == {
        "id",
        "run_id",
        "status",
        "core",
        "other",
        "memory_token_count",
        "error_message",
    }


def test_public_token_counter_accepts_a_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_counter(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return 37

    monkeypatch.setattr(
        "app.ai_chat.memory.token_budget.litellm.token_counter",
        fake_counter,
    )
    service = MemoryService()
    assert service.count_tokens("system + tools + current") == 37
    assert captured["text"] == "system + tools + current"
    assert "messages" not in captured


async def _conversation(isolated_db) -> int:
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).conversations.create(
            adapter="TestAdapter",
            subject={"type": "experience", "id": "1"},
            scope={"field": "background"},
            language="zh",
        )
        await session.commit()
        return row.id


async def _terminal_run(
    isolated_db,
    conversation_id: int,
    text: str,
    *,
    status: str = "completed",
    with_tool: bool = False,
) -> int:
    async with isolated_db.session() as session:
        repositories = RepositoryFactory().create(session)
        run = await repositories.runs.create(
            conversation_id=conversation_id,
            kind="user_turn",
            tools_enabled=True,
        )
        await repositories.messages.create(
            conversation_id=conversation_id,
            run_id=run.id,
            role="user",
            content=text,
            status="completed",
        )
        await repositories.messages.create(
            conversation_id=conversation_id,
            run_id=run.id,
            role="assistant",
            content=f"回答：{text}" if status == "completed" else f"部分：{text}",
            status="completed" if status == "completed" else "failed",
        )
        if with_tool:
            call = await repositories.tool_calls.create(
                conversation_id=conversation_id,
                run_id=run.id,
                tool_call_index=0,
                provider_tool_call_id=None,
                tool_name="change",
                arguments={"value": text},
            )
            call.status = "resolved" if status == "completed" else "executing"
            call.decision = "approve" if status == "completed" else None
            call.tool_result = {"outcome": "changed"} if status == "completed" else None
        await repositories.runs.transition(
            run.id,
            from_statuses={"running"},
            to_status=status,
            error_code="provider_failed" if status == "failed" else None,
        )
        await session.commit()
        return run.id


async def _running_run(isolated_db, conversation_id: int, text: str) -> int:
    async with isolated_db.session() as session:
        repositories = RepositoryFactory().create(session)
        run = await repositories.runs.create(
            conversation_id=conversation_id,
            kind="user_turn",
            tools_enabled=True,
        )
        await repositories.messages.create(
            conversation_id=conversation_id,
            run_id=run.id,
            role="user",
            content=text,
            status="completed",
        )
        await session.commit()
        return run.id


async def test_origin_run_uses_completed_and_failed_before_boundary(
    isolated_db,
) -> None:
    conversation_id = await _conversation(isolated_db)
    completed_id = await _terminal_run(
        isolated_db,
        conversation_id,
        "完成内容",
        with_tool=True,
    )
    failed_id = await _terminal_run(
        isolated_db,
        conversation_id,
        "失败内容",
        status="failed",
        with_tool=True,
    )
    await _terminal_run(
        isolated_db,
        conversation_id,
        "取消内容",
        status="cancelled",
    )
    boundary_id = await _running_run(isolated_db, conversation_id, "当前问题")

    async with isolated_db.session() as session:
        origin_runs = await OriginRunRepository(session).history_before(boundary_id)

    assert [run.run_id for run in origin_runs] == [completed_id, failed_id]
    assert [run.status for run in origin_runs] == ["completed", "failed"]
    assert origin_runs[1].error_code == "provider_failed"
    assert origin_runs[1].messages[-1]["status"] == "failed"
    assert origin_runs[1].tool_calls[0]["status"] == "executing"
    assert all(run.run_id != boundary_id for run in origin_runs)


class _GoalSummarizer:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def summarize(self, parent, origin_run):  # type: ignore[no-untyped-def]
        self.calls.append(origin_run.run_id)
        user = next(
            message["content"]
            for message in origin_run.messages
            if message["role"] == "user"
        )
        return [
            MemoryOperation(
                op="update",
                path="core.current_goal",
                value=str(user),
            ),
            MemoryOperation(
                op="update" if "summary_marker" in parent.other else "add",
                path="other.summary_marker",
                value=f"summary-only-run-{origin_run.run_id}",
            ),
        ]


def _fixed_budget(input_budget: int) -> MemoryTokenBudget:
    return MemoryTokenBudget(
        model="provider/model",
        max_tokens=100,
        input_budget=input_budget,
    )


async def test_summarizer_uses_non_streaming_litellm_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Router:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] | None = None

        async def acompletion(self, **kwargs: Any) -> Any:
            self.kwargs = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"operations":[]}'
                        )
                    )
                ]
            )

    router = _Router()
    config = SimpleNamespace(provider="openai", reasoning_effort="low")
    monkeypatch.setattr(
        "app.ai_chat.memory.summarizer.get_router",
        lambda: (router, config),
    )
    monkeypatch.setattr(
        "app.ai_chat.memory.summarizer.build_memory_token_budget",
        lambda *_args, **_kwargs: _fixed_budget(100),
    )
    monkeypatch.setattr(
        "app.ai_chat.memory.summarizer.count_request_tokens",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        "app.ai_chat.memory.summarizer._calculate_timeout",
        lambda *_args: 30,
    )

    operations = await MemorySummarizer().summarize(
        Memory(),
        OriginRun(
            run_id=1,
            kind="user_turn",
            status="completed",
            error_code=None,
            messages=({"role": "user", "content": "记住简洁回答"},),
            tool_calls=(),
        ),
    )

    assert operations == []
    assert router.kwargs is not None
    assert router.kwargs["model"] == "primary"
    assert router.kwargs["stream"] is False
    assert router.kwargs["reasoning_effort"] == "low"
    assert "tools" not in router.kwargs


async def test_history_prompt_uses_exact_memory_boundary_and_two_run_buffer(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = await _conversation(isolated_db)
    history_ids = [
        await _terminal_run(isolated_db, conversation_id, f"目标 {index}")
        for index in range(5)
    ]
    current_id = await _running_run(isolated_db, conversation_id, "当前问题")
    summarizer = _GoalSummarizer()
    service = MemoryService(summarizer)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_token_budget", lambda: _fixed_budget(100))

    def count_history_tokens(text: str) -> int:
        payload = json.loads(text)
        if "runs" in payload:
            return 10 + len(payload["runs"]) * 30
        if "run_id" in payload:
            return 30
        return 10

    monkeypatch.setattr(
        service,
        "count_tokens",
        count_history_tokens,
    )
    monkeypatch.setattr(memory_settings, "ai_chat_memory_token_cap", 10)
    monkeypatch.setattr(
        "app.ai_chat.memory.services.history_service._validate_memory_budget",
        lambda _document: 10,
    )

    prompt = await service.get_history_prompt(current_id, occupied_token=20)
    payload = json.loads(prompt)

    assert payload["memory"]["current_goal"] == "目标 2"
    assert [run["run_id"] for run in payload["runs"]] == history_ids[-2:]
    assert all(run["run_id"] != current_id for run in payload["runs"])
    async with isolated_db.session() as session:
        rows = list(
            (
                await session.execute(
                    select(AiChatRunMemory).order_by(AiChatRunMemory.run_id)
                )
            ).scalars()
        )
    assert [row.run_id for row in rows] == history_ids
    assert all(row.status == "completed" for row in rows)
    assert rows[2].core["current_goal"] == "目标 2"
    assert rows[3].other["summary_marker"] not in prompt
    assert rows[4].other["summary_marker"] not in prompt

    history_runs = await MemoryPersistenceService.load_history(current_id)
    assert [run.origin.run_id for run in history_runs] == history_ids
    assert [run.memory.run_id for run in history_runs if run.memory] == history_ids
    assert history_runs[2].memory is not None
    assert history_runs[2].memory.current_goal == "目标 2"

    await service.compress(current_id, occupied_token=20)
    assert summarizer.calls == history_ids


async def test_history_prompt_skips_compression_when_raw_history_fits(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = await _conversation(isolated_db)
    history_ids = [
        await _terminal_run(isolated_db, conversation_id, f"历史 {index}")
        for index in range(2)
    ]
    current_id = await _running_run(isolated_db, conversation_id, "当前问题")
    summarizer = _GoalSummarizer()
    service = MemoryService(summarizer)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_token_budget", lambda: _fixed_budget(100))
    monkeypatch.setattr(service, "count_tokens", lambda _text: 50)

    prompt = json.loads(
        await service.get_history_prompt(current_id, occupied_token=20)
    )

    assert [run["run_id"] for run in prompt["runs"]] == history_ids
    assert summarizer.calls == []
    async with isolated_db.session() as session:
        count = await session.scalar(
            select(func.count()).select_from(AiChatRunMemory)
        )
    assert count == 0


async def test_history_prompt_uses_existing_memory_without_compressing_again(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = await _conversation(isolated_db)
    await _terminal_run(isolated_db, conversation_id, "历史 0")
    await _terminal_run(isolated_db, conversation_id, "历史 1")
    current_id = await _running_run(isolated_db, conversation_id, "当前问题")
    service = MemoryService(_GoalSummarizer())  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_token_budget", lambda: _fixed_budget(100))

    def count_history_tokens(text: str) -> int:
        payload = json.loads(text)
        if "runs" in payload:
            return 10 + len(payload["runs"]) * 80
        if "run_id" in payload:
            return 80
        return 10

    monkeypatch.setattr(
        service,
        "count_tokens",
        count_history_tokens,
    )
    monkeypatch.setattr(memory_settings, "ai_chat_memory_token_cap", 10)
    monkeypatch.setattr(
        "app.ai_chat.memory.services.history_service._validate_memory_budget",
        lambda _document: 10,
    )
    await service.compress(current_id, occupied_token=20)

    async def unexpected_compress(_run_id: int, _occupied_token: int) -> None:
        raise AssertionError("existing memory should be reused")

    monkeypatch.setattr(service, "compress", unexpected_compress)

    prompt = json.loads(
        await service.get_history_prompt(current_id, occupied_token=20)
    )
    assert prompt["memory"]["current_goal"] == "历史 1"
    assert prompt["runs"] == []


class _FlakySummarizer(_GoalSummarizer):
    def __init__(self) -> None:
        super().__init__()
        self.should_fail = True

    async def summarize(self, parent, origin_run):  # type: ignore[no-untyped-def]
        if self.should_fail:
            self.should_fail = False
            raise RuntimeError("temporary summary failure")
        return await super().summarize(parent, origin_run)


async def test_failed_placeholder_is_retried_without_duplicate_row(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = await _conversation(isolated_db)
    history_id = await _terminal_run(isolated_db, conversation_id, "需要重试")
    current_id = await _running_run(isolated_db, conversation_id, "当前问题")
    summarizer = _FlakySummarizer()
    service = MemoryService(summarizer)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_token_budget", lambda: _fixed_budget(100))
    monkeypatch.setattr(service, "count_tokens", lambda _text: 1)
    monkeypatch.setattr(
        "app.ai_chat.memory.services.history_service._validate_memory_budget",
        lambda _document: 10,
    )

    with pytest.raises(MemoryCompactionError, match="token accounting"):
        await service.compress(current_id, occupied_token=0)
    async with isolated_db.session() as session:
        failed = await MemoryRepository(session).get_by_run_id(history_id)
        assert failed is not None
        assert failed.status == "failed"
        assert "temporary summary failure" in str(failed.error_message)

    await service.compress(current_id, occupied_token=0)
    async with isolated_db.session() as session:
        count = await session.scalar(
            select(func.count()).select_from(AiChatRunMemory)
        )
        completed = await MemoryRepository(session).get_by_run_id(history_id)
    assert count == 1
    assert completed is not None and completed.status == "completed"


async def test_compress_treats_run_id_as_exclusive_history_boundary(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = await _conversation(isolated_db)
    run_ids = [
        await _terminal_run(isolated_db, conversation_id, f"历史 {index}")
        for index in range(3)
    ]
    service = MemoryService(_GoalSummarizer())  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_token_budget", lambda: _fixed_budget(100))
    monkeypatch.setattr(service, "count_tokens", lambda _text: 1)
    monkeypatch.setattr(
        "app.ai_chat.memory.services.history_service._validate_memory_budget",
        lambda _document: 10,
    )

    await service.compress(run_ids[-1], occupied_token=0)

    async with isolated_db.session() as session:
        compressed = list(
            (
                await session.execute(
                    select(AiChatRunMemory).order_by(AiChatRunMemory.run_id)
                )
            ).scalars()
        )
    assert [row.run_id for row in compressed] == run_ids[:2]


async def test_context_full_when_occupied_content_exhausts_budget(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = await _conversation(isolated_db)
    current_id = await _running_run(isolated_db, conversation_id, "当前问题")
    service = MemoryService()
    monkeypatch.setattr(service, "_token_budget", lambda: _fixed_budget(50))
    monkeypatch.setattr(service, "count_tokens", lambda _text: 2)

    with pytest.raises(MemoryContextFullError, match="occupied_context_full"):
        await service.get_history_prompt(current_id, occupied_token=50)


async def test_conversation_delete_cascades_run_memories(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = await _conversation(isolated_db)
    await _terminal_run(isolated_db, conversation_id, "历史")
    current_id = await _running_run(isolated_db, conversation_id, "当前")
    service = MemoryService(_GoalSummarizer())  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_token_budget", lambda: _fixed_budget(100))
    monkeypatch.setattr(service, "count_tokens", lambda _text: 1)
    monkeypatch.setattr(
        "app.ai_chat.memory.services.history_service._validate_memory_budget",
        lambda _document: 10,
    )
    await service.compress(current_id, occupied_token=0)

    async with isolated_db.session() as session:
        await RepositoryFactory().create(session).conversations.delete(conversation_id)
        await session.commit()
    async with isolated_db.session() as session:
        count = await session.scalar(
            select(func.count()).select_from(AiChatRunMemory)
        )
    assert count == 0
