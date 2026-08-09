"""有界上下文与 Conversation Memory 的确定性测试。"""

from __future__ import annotations

import pytest

from app.ai_chat.adapters import AdapterRegistry
from app.ai_chat.errors import ContextFullError, MemoryCompactionError
from app.ai_chat.memory.operations import MemoryDocument, MemoryOperation, apply_operations
from app.ai_chat.memory.run_bundles import RunBundleBuilder
from app.ai_chat.memory.service import MemoryService
from app.ai_chat.context import ContextPlanner, PreparedContext
from app.ai_chat.model_request import ModelRequestSpec, count_request_tokens
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.services import AiChatService
from app.experience import ExperienceAdapter
from app.experience.schemas.experiences import ExperienceCreate
from app.experience.services.experience_service import ExperienceService
from app.config import settings
from app.ai_chat.streaming.events import AiChatEvent
from app.experience.routers.ai_chat import _business_event


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
            MemoryOperation(
                op="delete", path="other.example_to_follow"
            ),
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


def test_token_counter_receives_final_messages_and_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_counter(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return 37

    monkeypatch.setattr("app.ai_chat.model_request.litellm.token_counter", fake_counter)
    tool = {
        "type": "function",
        "function": {"name": "change", "description": "change", "parameters": {}},
    }
    spec = ModelRequestSpec(
        model="provider/model",
        config_fingerprint="fingerprint",
        tools=[tool],
        max_tokens=100,
        reasoning_effort=None,
        input_budget=1000,
    )
    messages = [{"role": "system", "content": "prompt"}]
    assert count_request_tokens(spec, messages) == 37
    assert captured == {
        "model": "provider/model",
        "messages": messages,
        "tools": [tool],
    }


def test_router_preserves_context_and_compaction_failure_codes() -> None:
    context = _business_event(
        AiChatEvent(
            "run.failed",
            {"code": "context_full", "reason": "current_user_too_large"},
        )
    )
    compaction = _business_event(
        AiChatEvent("run.failed", {"code": "memory_compaction_failed"})
    )
    assert context == AiChatEvent(
        "chat.error",
        {"code": "context_full", "reason": "current_user_too_large"},
    )
    assert compaction == AiChatEvent(
        "chat.error", {"code": "memory_compaction_failed"}
    )


async def _completed_run(
    isolated_db,
    conversation_id: int,
    text: str,
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
            content=f"答复：{text}",
            status="completed",
        )
        await repositories.runs.transition(
            run.id, from_statuses={"running"}, to_status="completed"
        )
        await session.commit()
        return run.id


class _GoalSummarizer:
    async def summarize(self, parent, bundle):  # type: ignore[no-untyped-def]
        user = next(message["content"] for message in bundle.messages if message["role"] == "user")
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


async def test_staged_chain_is_invisible_until_hash_checked_promotion(
    isolated_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = RepositoryFactory()
    async with isolated_db.session() as session:
        conversation = await repositories.create(session).conversations.create(
            adapter="test",
            subject={"type": "experience", "id": "1"},
            scope={"field": "background"},
            language="zh",
        )
        await session.commit()
    first_id = await _completed_run(isolated_db, conversation.id, "目标一")
    second_id = await _completed_run(isolated_db, conversation.id, "目标二")
    async with isolated_db.session() as session:
        bundles = await RunBundleBuilder(session).list_completed(conversation.id)

    monkeypatch.setattr(
        "app.ai_chat.memory.service.memory_token_count", lambda _document: 20
    )
    memory = MemoryService(repositories, _GoalSummarizer())  # type: ignore[arg-type]
    await memory.ensure_chain(
        conversation_id=conversation.id,
        bundles=bundles,
        target_run_id=second_id,
    )
    async with isolated_db.session() as session:
        repository = repositories.create(session).memory
        pointer, active = await repository.get_or_create(conversation.id)
        chain = await repository.chain_from(active.id)
        assert active.source_run_id is None
        assert [item.source_run_id for item in chain] == [first_id, second_id]
        assert pointer.active_snapshot_id == active.id

    promoted = await memory.promote(
        conversation_id=conversation.id,
        target_run_id=first_id,
        bundles=bundles,
    )
    assert promoted.source_run_id == first_id
    assert promoted.parent_snapshot_id is None
    async with isolated_db.session() as session:
        repository = repositories.create(session).memory
        pointer, active = await repository.get_or_create(conversation.id)
        chain = await repository.chain_from(active.id)
        assert pointer.active_snapshot_id == promoted.id
        assert [item.source_run_id for item in chain] == [second_id]

    async with isolated_db.session() as session:
        rows = await repositories.create(session).messages.list_completed(conversation.id)
        target = next(row for row in rows if row.run_id == second_id and row.role == "user")
        target.content = "已变化"
        await session.commit()
    async with isolated_db.session() as session:
        changed = await RunBundleBuilder(session).list_completed(conversation.id)
    with pytest.raises(MemoryCompactionError, match="source changed"):
        await memory.promote(
            conversation_id=conversation.id,
            target_run_id=second_id,
            bundles=changed,
        )


class _AlwaysFullPlanner:
    async def prepare(self, **_kwargs):  # type: ignore[no-untyped-def]
        raise ContextFullError(
            "current_user_too_large", used_tokens=101, budget_tokens=100
        )
        yield  # pragma: no cover


class _UnusedRunner:
    pass


async def test_preflight_failure_writes_no_messages_and_keeps_client_id_reusable(
    isolated_db,
) -> None:
    created = None
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(
            ExperienceCreate(title="上下文测试", background="背景")
        )
    registry = AdapterRegistry()
    registry.register(ExperienceAdapter())
    repositories = RepositoryFactory()
    service = AiChatService(registry, _UnusedRunner(), repositories)  # type: ignore[arg-type]
    service._planner = _AlwaysFullPlanner()  # noqa: SLF001
    conversation_id = await service.create_conversation(
        "ExperienceAdapter",
        {"type": "experience", "id": str(created.experience_id)},
        {"field": "background"},
    )

    for _ in range(2):
        events = [
            event
            async for event in service.stream_message(
                conversation_id, "很长的输入", "same-client-id"
            )
        ]
        assert [event.event for event in events] == ["context.usage", "run.failed"]
        assert events[-1].data == {
            "code": "context_full",
            "reason": "current_user_too_large",
        }

    async with isolated_db.session() as session:
        messages = await repositories.create(session).messages.list_completed(
            conversation_id
        )
        assert messages == []


async def test_startup_recovers_only_message_less_running_reservations(
    isolated_db,
) -> None:
    repositories = RepositoryFactory()
    async with isolated_db.session() as session:
        bound = repositories.create(session)
        conversation = await bound.conversations.create(
            adapter="test",
            subject={"type": "experience", "id": "1"},
            scope={"field": "background"},
            language="zh",
        )
        stale = await bound.runs.create(
            conversation_id=conversation.id,
            kind="user_turn",
            tools_enabled=True,
        )
        await session.commit()
    async with isolated_db.session() as session:
        count = await repositories.create(session).runs.recover_stale_preflight()
        await session.commit()
    assert count == 1
    async with isolated_db.session() as session:
        recovered = await repositories.create(session).runs.get(stale.id)
        assert recovered is not None
        assert recovered.status == "failed"
        assert recovered.error_code == "stale_preflight_recovered"


class _PlannerRunner:
    def prepare_request(self, **_kwargs):  # type: ignore[no-untyped-def]
        return ModelRequestSpec(
            model="gpt-4o-mini",
            config_fingerprint="test",
            tools=[],
            max_tokens=64,
            reasoning_effort=None,
            input_budget=250,
        )

    async def prepare_state(self, *, value, **_kwargs):  # type: ignore[no-untyped-def]
        return {
            "conversation_id": value["conversation_id"],
            "run_id": value["run_id"],
            "subject": value["subject"],
            "scope": value["scope"],
            "run_kind": value["run_kind"],
            "tools_enabled": value["tools_enabled"],
            "model_request": value["model_request"],
            "model_messages": [
                {"role": "system", "content": "fixed domain context"},
                *value["messages"],
            ],
        }


async def test_context_planner_compacts_complete_prefix_and_never_injects_stage(
    isolated_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    repositories = RepositoryFactory()
    async with isolated_db.session() as session:
        conversation = await repositories.create(session).conversations.create(
            adapter="test",
            subject={"type": "experience", "id": "1"},
            scope={"field": "background"},
            language="zh",
        )
        await session.commit()
    for index in range(6):
        await _completed_run(
            isolated_db,
            conversation.id,
            f"目标 {index} " + ("内容" * 12),
        )
    monkeypatch.setattr(settings, "ai_chat_memory_token_cap", 20)
    monkeypatch.setattr(
        "app.ai_chat.memory.service.memory_token_count", lambda _document: 10
    )
    memory = MemoryService(repositories, _GoalSummarizer())  # type: ignore[arg-type]
    planner = ContextPlanner(_PlannerRunner(), repositories, memory)  # type: ignore[arg-type]

    items = [
        item
        async for item in planner.prepare(
            conversation_id=conversation.id,
            run_id=999,
            kind="user_turn",
            user_content="继续",
            tools_enabled=True,
        )
    ]
    prepared = next(item for item in items if isinstance(item, PreparedContext))
    event_names = [item.event for item in items if hasattr(item, "event")]
    assert event_names[0] == "memory.compaction.started"
    assert event_names[-1] == "memory.compaction.completed"
    assert prepared.used_tokens <= prepared.budget_tokens
    assert 0 < len(prepared.recent_run_ids) < 6

    async with isolated_db.session() as session:
        repository = repositories.create(session).memory
        pointer, active = await repository.get_or_create(conversation.id)
        chain = await repository.chain_from(active.id)
        assert pointer.active_snapshot_id == active.id
        available_after_active = sum(
            bundle.last_sequence > active.covered_through_sequence
            for bundle in await RunBundleBuilder(session).list_completed(conversation.id)
        )
        assert len(chain) == min(2, available_after_active)
        assert chain
        staged_markers = [item.other["summary_marker"] for item in chain]
    visible = str(prepared.state["model_messages"])
    assert active.other["summary_marker"] in visible
    assert all(marker not in visible for marker in staged_markers)
