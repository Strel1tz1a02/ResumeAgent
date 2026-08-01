"""经历字段状态、Tool 和 Graph 的确定性业务测试。"""

from __future__ import annotations

import sqlite3

import pytest

from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.checkpoint import CheckpointLifecycle
from app.ai_chat.graph import GraphRunner
from app.ai_chat.registry import AdapterRegistry
from app.ai_chat.service import AiChatService
from app.ai_chat.model import ModelCompleted, TextDelta, ToolCallsCompleted
from app.ai_chat.tools.buffer import AssembledToolCall
from app.ai_chat.runtime import AiChatRuntime
from app.ai_chat.tools.handler import ApprovalProposal, ToolContext
from app.ai_chat.tools.lifecycle import ToolLifecycle
from app.database import Database
from app.experience_ai_chat import ExperienceAdapter
from app.experience_ai_chat.tools.evidence_append import (
    EvidenceAppendArguments,
    EvidenceAppendHandler,
)
from app.experience_ai_chat.tools.field_overwrite import (
    FieldOverwriteArguments,
    FieldOverwriteHandler,
)
from app.schemas.experiences import ExperienceCreate, ExperienceUpdate
from app.services.experience_service import ExperienceConflictError, ExperienceService


class _UnusedModel:
    async def stream(self, **kwargs):  # type: ignore[no-untyped-def]
        if False:
            yield kwargs


class _ConversationModel:
    """按 tools_enabled 生成确定性 opening、提案和续答。"""

    async def stream(self, *, tools_enabled: bool, **kwargs):  # type: ignore[no-untyped-def]
        if not tools_enabled:
            yield TextDelta("已处理")
            yield ModelCompleted("stop")
            return
        yield ToolCallsCompleted(
            (
                AssembledToolCall(
                    index=0,
                    provider_id="call-1",
                    name="field_overwrite",
                    arguments={"proposed_value": "新背景"},
                ),
            )
        )
        yield ModelCompleted("tool_calls")


async def test_create_initializes_states_and_group_revision(isolated_db) -> None:
    """身份保存单元任一真实变更都会同步推进 kind/title revision。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(
            ExperienceCreate(title="旧标题")
        )
    states = {(state.key, state.ref_id): state for state in created.field_states}
    assert states[("title", None)].revision == 0
    assert states[("kind", None)].revision == 0

    async with isolated_db.session() as session:
        updated = await ExperienceService(session).patch(
            created.experience_id,
            ExperienceUpdate(
                title="新标题", expected_field_revisions={"title": 0}
            ),
        )
    states = {(state.key, state.ref_id): state for state in updated.field_states}
    assert states[("title", None)].revision == 1
    assert states[("kind", None)].revision == 1

    async with isolated_db.session() as session:
        with pytest.raises(ExperienceConflictError):
            await ExperienceService(session).patch(
                created.experience_id,
                ExperienceUpdate(
                    title="过期覆盖", expected_field_revisions={"title": 0}
                ),
            )


async def test_field_overwrite_proposal_and_apply(isolated_db) -> None:
    """字段 Tool 先产生提案，审批时再次使用 revision guard。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(
            ExperienceCreate(title="Agent", background="旧背景")
        )
    state = next(item for item in created.field_states if item.key == "background")
    context = ToolContext(
        conversation_id=1,
        run_id=1,
        tool_call_id=1,
        subject={"type": "experience", "id": str(created.experience_id)},
        target={"key": "background", "ref_id": None},
        adapter_context={
            "target_revision_at_generation_start": state.revision,
            "normalized_target_value_at_generation_start": "旧背景",
        },
    )
    handler = FieldOverwriteHandler()
    proposal = await handler.validate(
        context, FieldOverwriteArguments(proposed_value="新背景")
    )
    assert isinstance(proposal, ApprovalProposal)
    result = await handler.resolve(
        context,
        FieldOverwriteArguments(proposed_value="新背景"),
        proposal.proposal_payload,
        proposal.guard_payload,
        "approve",
    )
    assert result.payload["outcome"] == "applied"
    assert result.payload["value"] == "新背景"


async def test_evidence_append_always_appends_database_id(isolated_db) -> None:
    """Evidence 创建不接受模型 ID，并只追加数据库生成 ID。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(ExperienceCreate(title="Agent"))
    collection = next(item for item in created.field_states if item.key == "evidence_new")
    context = ToolContext(
        conversation_id=2,
        run_id=2,
        tool_call_id=2,
        subject={"type": "experience", "id": str(created.experience_id)},
        target={"key": "evidence_new", "ref_id": None},
        adapter_context={
            "target_revision_at_generation_start": collection.revision,
            "normalized_target_value_at_generation_start": [],
        },
    )
    arguments = EvidenceAppendArguments(
        item={"action": "搭建发布流水线", "result": "自动发布", "metrics": None}
    )
    handler = EvidenceAppendHandler()
    proposal = await handler.validate(context, arguments)
    assert isinstance(proposal, ApprovalProposal)
    result = await handler.resolve(
        context,
        arguments,
        proposal.proposal_payload,
        proposal.guard_payload,
        "approve",
    )
    assert result.payload["outcome"] == "applied"
    assert result.payload["evidence_ids"] == [result.payload["evidence_id"]]


def test_graph_has_one_continue_without_tools_node() -> None:
    """所有 Tool 分支必须复用唯一续跑节点。"""
    adapter = ExperienceAdapter()
    runtime = AiChatRuntime(
        _UnusedModel(),  # type: ignore[arg-type]
        adapter.get_tool_handlers(),
        ToolLifecycle(RepositoryFactory()),
    )
    graph = adapter.build_graph(runtime)
    assert list(graph.nodes).count("continue_without_tools") == 1


def test_migration_drops_raw_input_and_records_version(tmp_path) -> None:
    """旧库原文列通过一次性迁移删除，而不是运行时字段补建。"""
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE experience_items ("
            "experience_id INTEGER PRIMARY KEY, kind VARCHAR(32), title VARCHAR(200), "
            "organization VARCHAR(200), role VARCHAR(160), location VARCHAR(160), "
            "start_date VARCHAR(7), end_date VARCHAR(7), is_current BOOLEAN, raw_input TEXT, "
            "background TEXT, evidence_ids JSON, technologies JSON, tags JSON, notes TEXT, "
            "status VARCHAR(16), completeness INTEGER, archived_at VARCHAR, created_at VARCHAR, updated_at VARCHAR)"
        )
        connection.execute(
            "INSERT INTO experience_items VALUES "
            "(1,'project','Agent',NULL,NULL,NULL,NULL,NULL,0,'必须销毁',NULL,'[]','[]','[]',NULL,'draft',0,NULL,'now','now')"
        )
    database = Database(path)
    with database._sync() as session:  # noqa: SLF001 - migration verification
        columns = session.connection().exec_driver_sql(
            "PRAGMA table_info(experience_items)"
        ).mappings().all()
        migration = session.connection().exec_driver_sql(
            "SELECT name FROM schema_migrations"
        ).scalar_one()
    assert "raw_input" not in {column["name"] for column in columns}
    assert migration == "2026_08_01_experience_field_states"


async def test_real_graph_interrupt_approve_and_single_continuation(
    isolated_db, tmp_path
) -> None:
    """真实 LangGraph/checkpointer 能暂停审批、应用字段并无 Tool 续跑。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(
            ExperienceCreate(title="Agent", background="旧背景")
        )
    adapter = ExperienceAdapter()
    registry = AdapterRegistry()
    registry.register(adapter)
    repositories = RepositoryFactory()
    lifecycle = ToolLifecycle(repositories)
    checkpoints = CheckpointLifecycle(tmp_path / "checkpoints.db")
    saver = await checkpoints.start()
    runner = GraphRunner(registry, saver, _ConversationModel(), lifecycle)  # type: ignore[arg-type]
    service = AiChatService(registry, runner, lifecycle, repositories)
    try:
        conversation_id = await service.create_conversation(
            "ExperienceAdapter",
            {"type": "experience", "id": str(created.experience_id)},
            {"key": "background", "ref_id": None},
        )
        events = [
            event
            async for event in service.stream_message(
                conversation_id, "请改写背景", "message-1"
            )
        ]
        proposal = next(event for event in events if event.event == "proposal.requested")
        proposal_id = int(proposal.data["proposal_id"])
        continued = [
            event
            async for event in service.resolve_proposal(
                proposal_id, "approve", "resolution-1"
            )
        ]
        assert any(event.event == "field_overwrite.applied" for event in continued)
        assert sum(event.event == "assistant.completed" for event in continued) == 1
        async with isolated_db.session() as session:
            detail = await ExperienceService(session).get(created.experience_id)
        assert detail.background == "新背景"
    finally:
        await checkpoints.close()
