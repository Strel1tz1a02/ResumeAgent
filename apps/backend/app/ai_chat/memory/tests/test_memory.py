"""历史 Prompt、终态 Run 和按 Run 压缩状态测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select, text

from app.ai_chat.memory.errors import (
    MemoryCompactionTimeoutError,
    MemoryContextFullError,
)
from app.ai_chat.memory.operations import MemoryOperation, apply_operations
from app.ai_chat.memory.runs import (
    Memory,
    OriginRun,
    Run,
)
from app.ai_chat.memory.services.memory_persistence_service import (
    MemoryPersistenceService,
)
from app.ai_chat.memory.services.memory_service import MemoryService
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


def test_internal_string_token_counter_accepts_a_string(
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
    assert service._count_string_tokens("system + tools + current") == 37
    assert captured["text"] == "system + tools + current"
    assert "messages" not in captured


def test_request_token_counter_includes_tool_definitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_counter(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return 41

    monkeypatch.setattr(
        "app.ai_chat.memory.token_budget.litellm.token_counter",
        fake_counter,
    )
    messages = [{"role": "user", "content": "current"}]
    tools = [{"type": "function", "function": {"name": "change"}}]

    service = MemoryService()
    assert service.count_request_tokens(messages, tools=tools) == 41
    assert captured["messages"] == messages
    assert captured["tools"] == tools


async def test_prepare_request_messages_injects_history_after_system_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MemoryService()
    counted: list[list[dict[str, Any]]] = []
    validated: list[list[dict[str, Any]]] = []
    tools = [{"type": "function", "function": {"name": "change"}}]

    def fake_count(messages, *, tools=None):  # type: ignore[no-untyped-def]
        assert tools is not None
        counted.append(messages)
        return 123

    async def fake_history(run_id, occupied_token):  # type: ignore[no-untyped-def]
        assert run_id == 7
        assert occupied_token == 123
        return '{"memory":{"current_goal":"remembered"},"runs":[]}'

    def fake_validate(messages, *, tools=None):  # type: ignore[no-untyped-def]
        assert tools is not None
        validated.append(messages)
        return 200

    monkeypatch.setattr(service, "count_request_tokens", fake_count)
    monkeypatch.setattr(service, "_get_history_prompt", fake_history)
    monkeypatch.setattr(service, "validate_request", fake_validate)
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "system", "content": "domain truth"},
        {"role": "user", "content": "current question"},
    ]

    prepared = await service.prepare_request_messages(
        7,
        messages,
        tools=tools,
    )

    assert [message["role"] for message in prepared] == [
        "system",
        "system",
        "user",
        "user",
    ]
    assert "remembered" in str(prepared[2]["content"])
    assert prepared[3] == messages[2]
    assert "remembered" not in str(counted[0][2]["content"])
    assert validated == [prepared]


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


async def test_current_run_messages_exclude_older_transcript(isolated_db) -> None:
    conversation_id = await _conversation(isolated_db)
    await _terminal_run(isolated_db, conversation_id, "old transcript")
    current_id = await _running_run(
        isolated_db,
        conversation_id,
        "current question",
    )

    async with isolated_db.session() as session:
        rows = await RepositoryFactory().create(
            session
        ).messages.list_completed_for_run(current_id)

    assert [(row.role, row.content) for row in rows] == [
        ("user", "current question")
    ]


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
        "_count_string_tokens",
        count_history_tokens,
    )
    monkeypatch.setattr(memory_settings, "ai_chat_memory_token_cap", 10)
    monkeypatch.setattr(
        "app.ai_chat.memory.services.memory_service._validate_memory_budget",
        lambda _document: 10,
    )

    await service.compact_run(history_ids[-1])
    prompt = await service._get_history_prompt(current_id, occupied_token=20)
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

    assert await service.compact_run(history_ids[-1])
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
    monkeypatch.setattr(service, "_count_string_tokens", lambda _text: 50)

    prompt = json.loads(
        await service._get_history_prompt(current_id, occupied_token=20)
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
    latest_history_id = await _terminal_run(
        isolated_db, conversation_id, "历史 1"
    )
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
        "_count_string_tokens",
        count_history_tokens,
    )
    monkeypatch.setattr(memory_settings, "ai_chat_memory_token_cap", 10)
    monkeypatch.setattr(
        "app.ai_chat.memory.services.memory_service._validate_memory_budget",
        lambda _document: 10,
    )
    await service.compact_run(latest_history_id)

    prompt = json.loads(
        await service._get_history_prompt(current_id, occupied_token=20)
    )
    assert prompt["memory"]["current_goal"] == "历史 1"
    assert prompt["runs"] == []


class _FailRunSummarizer(_GoalSummarizer):
    def __init__(self, fail_run_id: int) -> None:
        super().__init__()
        self.fail_run_id = fail_run_id

    async def summarize(self, parent, origin_run):  # type: ignore[no-untyped-def]
        if origin_run.run_id == self.fail_run_id:
            raise RuntimeError("summary retries exhausted")
        return await super().summarize(parent, origin_run)


async def test_skipped_run_keeps_chain_open_for_later_run(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = await _conversation(isolated_db)
    parent_id = await _terminal_run(isolated_db, conversation_id, "已有目标")
    skipped_id = await _terminal_run(isolated_db, conversation_id, "会失败")
    completed_id = await _terminal_run(isolated_db, conversation_id, "继续压缩")
    summarizer = _FailRunSummarizer(skipped_id)
    service = MemoryService(summarizer)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_token_budget", lambda: _fixed_budget(100))
    monkeypatch.setattr(service, "_count_string_tokens", lambda _text: 1)
    monkeypatch.setattr(
        "app.ai_chat.memory.services.memory_service._validate_memory_budget",
        lambda _document: 10,
    )

    assert await service.compact_run(completed_id)
    async with isolated_db.session() as session:
        parent = await MemoryRepository(session).get_by_run_id(parent_id)
        skipped = await MemoryRepository(session).get_by_run_id(skipped_id)
        completed = await MemoryRepository(session).get_by_run_id(completed_id)
        count = await session.scalar(
            select(func.count()).select_from(AiChatRunMemory)
        )
        assert parent is not None and parent.status == "completed"
        assert skipped is not None
        assert skipped.status == "skipped"
        assert skipped.core == parent.core
        assert skipped.other == parent.other
        assert "summary retries exhausted" in str(skipped.error_message)
    assert count == 3
    assert completed is not None and completed.status == "completed"
    assert completed.core["current_goal"] == "继续压缩"


async def test_compact_run_includes_target_terminal_run(
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
    monkeypatch.setattr(service, "_count_string_tokens", lambda _text: 1)
    monkeypatch.setattr(
        "app.ai_chat.memory.services.memory_service._validate_memory_budget",
        lambda _document: 10,
    )

    assert await service.compact_run(run_ids[-1])

    async with isolated_db.session() as session:
        compressed = list(
            (
                await session.execute(
                    select(AiChatRunMemory).order_by(AiChatRunMemory.run_id)
                )
            ).scalars()
        )
    assert [row.run_id for row in compressed] == run_ids


async def test_history_prompt_times_out_instead_of_compressing_in_web_process(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = await _conversation(isolated_db)
    await _terminal_run(isolated_db, conversation_id, "历史 0")
    await _terminal_run(isolated_db, conversation_id, "历史 1")
    current_id = await _running_run(isolated_db, conversation_id, "当前问题")
    summarizer = _GoalSummarizer()
    service = MemoryService(summarizer)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_token_budget", lambda: _fixed_budget(100))

    def count_history_tokens(text: str) -> int:
        payload = json.loads(text)
        if "runs" in payload:
            return 10 + len(payload["runs"]) * 80
        if "run_id" in payload:
            return 80
        return 10

    monkeypatch.setattr(service, "_count_string_tokens", count_history_tokens)
    monkeypatch.setattr(memory_settings, "ai_chat_memory_token_cap", 10)
    monkeypatch.setattr(
        memory_settings,
        "ai_chat_memory_wait_timeout_seconds",
        0,
    )

    with pytest.raises(MemoryCompactionTimeoutError):
        await service._get_history_prompt(current_id, occupied_token=20)

    assert summarizer.calls == []


async def test_context_full_when_occupied_content_exhausts_budget(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = await _conversation(isolated_db)
    current_id = await _running_run(isolated_db, conversation_id, "当前问题")
    service = MemoryService()
    monkeypatch.setattr(service, "_token_budget", lambda: _fixed_budget(50))
    monkeypatch.setattr(service, "_count_string_tokens", lambda _text: 2)

    with pytest.raises(MemoryContextFullError, match="occupied_context_full"):
        await service._get_history_prompt(current_id, occupied_token=50)


async def test_conversation_delete_cascades_run_memories(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = await _conversation(isolated_db)
    history_id = await _terminal_run(isolated_db, conversation_id, "历史")
    await _running_run(isolated_db, conversation_id, "当前")
    service = MemoryService(_GoalSummarizer())  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_token_budget", lambda: _fixed_budget(100))
    monkeypatch.setattr(service, "_count_string_tokens", lambda _text: 1)
    monkeypatch.setattr(
        "app.ai_chat.memory.services.memory_service._validate_memory_budget",
        lambda _document: 10,
    )
    assert await service.compact_run(history_id)

    async with isolated_db.session() as session:
        await RepositoryFactory().create(session).conversations.delete(conversation_id)
        await session.commit()
    async with isolated_db.session() as session:
        count = await session.scalar(
            select(func.count()).select_from(AiChatRunMemory)
        )
    assert count == 0
