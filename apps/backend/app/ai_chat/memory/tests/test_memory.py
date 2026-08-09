"""Conversation Memory 单一 Messages 边界的确定性测试。"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select

from app.ai_chat.adapters import AdapterRegistry
from app.ai_chat.memory.errors import MemoryCompactionError, MemoryContextFullError
from app.ai_chat.memory.models import (
    AiChatConversationMemory,
    AiChatConversationMemorySnapshot,
)
from app.ai_chat.memory.operations import MemoryDocument, MemoryOperation, apply_operations
from app.ai_chat.memory.repository import MemoryRepository
from app.ai_chat.memory.run_bundles import RunBundleBuilder
from app.ai_chat.memory.service import MemoryContextService, _SnapshotService
from app.ai_chat.memory.settings import memory_settings
from app.ai_chat.memory.token_budget import MemoryTokenBudget, count_request_tokens
from app.ai_chat.repositories import RepositoryFactory


def test_operations_enforce_core_and_other_boundaries() -> None:
    parent = MemoryDocument(
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
                op="add", path="other.preferred_wording", value="保留口语感"
            ),
        ],
    )
    assert result.current_goal == "完善项目经历"
    assert result.preferences == ["使用简洁措辞"]
    assert result.other == {"preferred_wording": "保留口语感"}

    with pytest.raises(ValueError, match="add can only create other"):
        MemoryOperation(op="add", path="core.constraints", value=["必须真实"])
    with pytest.raises(ValueError, match="conflicting operations"):
        apply_operations(
            parent,
            [
                MemoryOperation(
                    op="update", path="core.preferences", value=["简洁"]
                ),
                MemoryOperation(op="delete", path="core.preferences"),
            ],
        )


def test_token_counter_receives_rendered_messages_and_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_counter(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return 37

    monkeypatch.setattr(
        "app.ai_chat.memory.token_budget.litellm.token_counter", fake_counter
    )
    tool = {
        "type": "function",
        "function": {"name": "change", "description": "change", "parameters": {}},
    }
    budget = MemoryTokenBudget(
        model="provider/model",
        tools=[tool],
        max_tokens=100,
        input_budget=1000,
    )
    messages = [{"role": "system", "content": "prompt"}]
    assert count_request_tokens(budget, messages) == 37
    assert captured == {
        "model": "provider/model",
        "messages": messages,
        "tools": [tool],
    }


async def _conversation(isolated_db, adapter: str = "TestAdapter") -> int:
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).conversations.create(
            adapter=adapter,
            subject={"type": "experience", "id": "1"},
            scope={"field": "background"},
            language="zh",
        )
        await session.commit()
        return row.id


async def _completed_run(isolated_db, conversation_id: int, text: str) -> int:
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
            content=f"答复：{text}",
            status="completed",
        )
        await repositories.runs.transition(
            run.id, from_statuses={"running"}, to_status="completed"
        )
        await session.commit()
        return run.id


async def _running_user(isolated_db, conversation_id: int, text: str) -> int:
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
            content="partial output must stay invisible",
            status="generating",
        )
        await session.commit()
        return run.id


async def test_run_bundle_builder_excludes_non_completed_runs(isolated_db) -> None:
    conversation_id = await _conversation(isolated_db)
    async with isolated_db.session() as session:
        repositories = RepositoryFactory().create(session)
        for status in ("failed", "cancelled", "suspended"):
            run = await repositories.runs.create(
                conversation_id=conversation_id,
                kind="user_turn",
                tools_enabled=True,
            )
            await repositories.messages.create(
                conversation_id=conversation_id,
                run_id=run.id,
                role="user",
                content=f"{status} user",
                status="completed",
            )
            await repositories.messages.create(
                conversation_id=conversation_id,
                run_id=run.id,
                role="assistant",
                content=f"{status} assistant",
                status="completed",
            )
            await repositories.runs.transition(
                run.id, from_statuses={"running"}, to_status=status
            )
        await session.commit()
    async with isolated_db.session() as session:
        assert await RunBundleBuilder(session).list_completed(conversation_id) == []


class _GoalSummarizer:
    async def summarize(self, parent, bundle):  # type: ignore[no-untyped-def]
        user = next(
            message["content"]
            for message in bundle.messages
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
                value=f"summary-only-run-{bundle.run_id}",
            ),
        ]


class _TestAdapter:
    @classmethod
    def adapter_name(cls) -> str:
        return "TestAdapter"

    async def parse_input(self, value):  # type: ignore[no-untyped-def]
        return {
            "model_messages": [
                {"role": "system", "content": "fixed domain context"},
                *value["messages"],
            ]
        }

    def get_tool_handlers(self):  # type: ignore[no-untyped-def]
        return {}


async def test_snapshot_stage_is_invisible_and_source_hash_is_rechecked(
    isolated_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id = await _conversation(isolated_db)
    first_id = await _completed_run(isolated_db, conversation_id, "目标一")
    second_id = await _completed_run(isolated_db, conversation_id, "目标二")
    async with isolated_db.session() as session:
        bundles = await RunBundleBuilder(session).list_completed(conversation_id)

    monkeypatch.setattr(
        "app.ai_chat.memory.service.validate_memory_budget", lambda _document: 20
    )
    snapshots = _SnapshotService(_GoalSummarizer())  # type: ignore[arg-type]
    await snapshots.ensure_chain(
        conversation_id=conversation_id,
        bundles=bundles,
        target_run_id=second_id,
    )
    async with isolated_db.session() as session:
        repository = MemoryRepository(session)
        pointer, active = await repository.get_or_create(conversation_id)
        chain = await repository.chain_from(active.id)
        assert active.source_run_id is None
        assert [item.source_run_id for item in chain] == [first_id, second_id]
        assert pointer.active_snapshot_id == active.id

    promoted = await snapshots.promote(
        conversation_id=conversation_id,
        target_run_id=first_id,
        bundles=bundles,
    )
    assert promoted.source_run_id == first_id
    async with isolated_db.session() as session:
        rows = await RepositoryFactory().create(session).messages.list_completed(
            conversation_id
        )
        target = next(
            row for row in rows if row.run_id == second_id and row.role == "user"
        )
        target.content = "来源已变化"
        await session.commit()
    async with isolated_db.session() as session:
        changed = await RunBundleBuilder(session).list_completed(conversation_id)
    with pytest.raises(MemoryCompactionError, match="source changed"):
        await snapshots.promote(
            conversation_id=conversation_id,
            target_run_id=second_id,
            bundles=changed,
        )


async def test_conversation_delete_cascades_memory_pointer_and_snapshots(
    isolated_db,
) -> None:
    conversation_id = await _conversation(isolated_db)
    await _SnapshotService().active(conversation_id)
    async with isolated_db.session() as session:
        await RepositoryFactory().create(session).conversations.delete(conversation_id)
        await session.commit()
    async with isolated_db.session() as session:
        pointers = await session.scalar(
            select(func.count()).select_from(AiChatConversationMemory)
        )
        snapshots = await session.scalar(
            select(func.count()).select_from(AiChatConversationMemorySnapshot)
        )
    assert pointers == 0
    assert snapshots == 0


async def test_public_service_returns_only_active_recent_and_current_messages(
    isolated_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id = await _conversation(isolated_db)
    completed_ids = [
        await _completed_run(isolated_db, conversation_id, f"目标 {index}")
        for index in range(8)
    ]
    current_id = await _running_user(isolated_db, conversation_id, "本轮问题")

    def fixed_counter(**kwargs):  # type: ignore[no-untyped-def]
        return len(kwargs.get("messages") or []) * 20

    monkeypatch.setattr(
        "app.ai_chat.memory.token_budget.litellm.token_counter", fixed_counter
    )
    monkeypatch.setattr(memory_settings, "ai_chat_input_cap", 140)
    monkeypatch.setattr(memory_settings, "ai_chat_safety_margin", 0)
    monkeypatch.setattr(memory_settings, "ai_chat_memory_token_cap", 20)
    monkeypatch.setattr(
        "app.ai_chat.memory.service.validate_memory_budget", lambda _document: 10
    )
    registry = AdapterRegistry()
    registry.register(_TestAdapter())  # type: ignore[arg-type]
    service = MemoryContextService(
        registry, RepositoryFactory(), _GoalSummarizer()  # type: ignore[arg-type]
    )

    messages = await service.get_context_messages(
        conversation_id=conversation_id,
        run_id=current_id,
        run_kind="user_turn",
        tools_enabled=True,
    )

    assert isinstance(messages, list)
    assert messages[-1] == {"role": "user", "content": "本轮问题"}
    assert all("partial output" not in str(message) for message in messages)
    assert messages[0]["content"].startswith(
        "CONVERSATION_MEMORY_DERIVED_NON_AUTHORITATIVE"
    )
    async with isolated_db.session() as session:
        repository = MemoryRepository(session)
        _, active = await repository.get_or_create(conversation_id)
        chain = await repository.chain_from(active.id)
        assert active.source_run_id == completed_ids[-3]
        assert [item.source_run_id for item in chain] == completed_ids[-2:]
        staged_markers = [item.other["summary_marker"] for item in chain]
    assert active.other["summary_marker"] in str(messages)
    assert all(marker not in str(messages) for marker in staged_markers)


async def test_memory_failure_is_exposed_to_future_caller(
    isolated_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id = await _conversation(isolated_db)
    current_id = await _running_user(isolated_db, conversation_id, "仍需回答")
    registry = AdapterRegistry()
    registry.register(_TestAdapter())  # type: ignore[arg-type]
    service = MemoryContextService(registry, RepositoryFactory())

    async def fail_select(**_kwargs):  # type: ignore[no-untyped-def]
        raise MemoryCompactionError("broken memory")

    monkeypatch.setattr(service, "_select_messages", fail_select)
    with pytest.raises(MemoryCompactionError, match="broken memory"):
        await service.get_context_messages(
            conversation_id=conversation_id,
            run_id=current_id,
            run_kind="user_turn",
            tools_enabled=True,
        )


async def test_fixed_context_overflow_is_exposed_to_future_caller(
    isolated_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id = await _conversation(isolated_db)
    current_id = await _running_user(isolated_db, conversation_id, "超长输入")
    registry = AdapterRegistry()
    registry.register(_TestAdapter())  # type: ignore[arg-type]
    service = MemoryContextService(registry, RepositoryFactory())
    monkeypatch.setattr(memory_settings, "ai_chat_input_cap", 100)
    monkeypatch.setattr(memory_settings, "ai_chat_safety_margin", 0)
    monkeypatch.setattr(
        "app.ai_chat.memory.token_budget.litellm.token_counter",
        lambda **_kwargs: 101,
    )

    with pytest.raises(MemoryContextFullError, match="fixed_input_too_large"):
        await service.get_context_messages(
            conversation_id=conversation_id,
            run_id=current_id,
            run_kind="user_turn",
            tools_enabled=True,
        )


async def test_public_service_rejects_run_from_another_conversation(
    isolated_db,
) -> None:
    first_conversation = await _conversation(isolated_db)
    second_conversation = await _conversation(isolated_db)
    foreign_run = await _running_user(isolated_db, second_conversation, "私有消息")
    registry = AdapterRegistry()
    registry.register(_TestAdapter())  # type: ignore[arg-type]
    service = MemoryContextService(registry, RepositoryFactory())

    with pytest.raises(ValueError, match="run does not belong"):
        await service.get_context_messages(
            conversation_id=first_conversation,
            run_id=foreign_run,
            run_kind="user_turn",
            tools_enabled=True,
        )
