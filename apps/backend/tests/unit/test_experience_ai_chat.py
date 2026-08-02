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
from app.ai_chat.types import SubjectRef, TargetRef
from app.database import Database
from app.experience_ai_chat import ExperienceAdapter
from app.experience_ai_chat.graph import ExperienceInputState
from app.experience_ai_chat.prompts import system_prompt
from app.experience_ai_chat.tools.content_change import (
    ContentChangeArguments,
    ContentChangeHandler,
)
from app.schemas.evidence_items import EvidenceCreate
from app.schemas.experiences import ExperienceCreate, ExperienceUpdate
from app.services.evidence_service import EvidenceService
from app.services.experience_ai_mutation_service import ExperienceAiMutationService
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
                    name="content_change",
                    arguments={
                        "target": {"key": "background", "evidence_id": None},
                        "suggested_content": "新背景",
                    },
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


async def test_content_change_routes_field_proposal_and_apply(isolated_db) -> None:
    """统一 Tool 将字段建议路由到 Service，并在审批时二次校验。"""
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
    handler = ContentChangeHandler()
    arguments = ContentChangeArguments(
        target={"key": "background", "evidence_id": None},
        suggested_content="新背景",
    )
    proposal = await handler.invoke(
        context, arguments
    )
    assert isinstance(proposal, ApprovalProposal)
    result = await handler.resolve(
        context,
        arguments,
        proposal.proposal_payload,
        proposal.guard_payload,
        "approve",
    )
    assert result.payload["outcome"] == "applied"
    assert result.payload["value"] == "新背景"


async def test_change_validation_is_owned_by_service(isolated_db) -> None:
    """模型 target 与会话绑定不一致时由 Service 返回 invalid_target。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(
            ExperienceCreate(title="Agent", background="旧背景")
        )
    state = next(item for item in created.field_states if item.key == "background")
    async with isolated_db.session() as session:
        result = await ExperienceAiMutationService(session).prepare_field_change(
            created.experience_id,
            "notes",
            None,
            "越权修改",
            bound_key="background",
            bound_ref_id=None,
            expected_revision=state.revision,
        )
    assert result == {"outcome": "invalid_target", "operation": "content_change"}


async def test_content_change_routes_evidence_append(isolated_db) -> None:
    """统一 Tool 将新增 Evidence 建议路由到追加 Service。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(ExperienceCreate(title="Agent"))
    collection = next(item for item in created.field_states if item.key == "evidence_new")
    context = ToolContext(
        conversation_id=2,
        run_id=2,
        tool_call_id=2,
        subject={"type": "experience", "id": str(created.experience_id)},
        target={"key": "evidence", "ref_id": None},
        adapter_context={
            "target_revision_at_generation_start": collection.revision,
            "normalized_target_value_at_generation_start": [],
        },
    )
    arguments = ContentChangeArguments(
        target={"key": "evidence", "evidence_id": None},
        suggested_content={
            "action": "搭建发布流水线",
            "result": "自动发布",
            "metrics": None,
        },
    )
    handler = ContentChangeHandler()
    proposal = await handler.invoke(context, arguments)
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


async def test_content_change_overwrites_one_complete_evidence_item(isolated_db) -> None:
    """共享 Evidence 会话按 ID 整体覆盖一项，不修改其他 EvidenceItem。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(ExperienceCreate(title="Agent"))
    async with isolated_db.session() as session:
        await EvidenceService(session).create(
            created.experience_id,
            EvidenceCreate(action="旧行动一", result="旧结果一", metrics="旧指标一"),
        )
    async with isolated_db.session() as session:
        with_both = await EvidenceService(session).create(
            created.experience_id,
            EvidenceCreate(action="旧行动二", result="旧结果二", metrics="旧指标二"),
        )
    first, second = with_both.evidence_items
    first_revision = next(
        state.revision
        for state in with_both.field_states
        if state.key == "action" and state.ref_id == first.id
    )
    context = ToolContext(
        conversation_id=3,
        run_id=3,
        tool_call_id=3,
        subject={"type": "experience", "id": str(created.experience_id)},
        target={"key": "evidence", "ref_id": None},
        adapter_context={
            "target_revision_at_generation_start": 2,
            "normalized_target_value_at_generation_start": [],
            "evidence_revisions_at_generation_start": {
                str(first.id): first_revision,
            },
        },
    )
    arguments = ContentChangeArguments(
        target={"key": "evidence", "evidence_id": first.id},
        suggested_content={
            "action": "新行动一",
            "result": "新结果一",
            "metrics": "新指标一",
        },
    )
    handler = ContentChangeHandler()
    proposal = await handler.invoke(context, arguments)
    assert isinstance(proposal, ApprovalProposal)
    result = await handler.resolve(
        context,
        arguments,
        proposal.proposal_payload,
        proposal.guard_payload,
        "approve",
    )
    assert result.payload["target"] == {"key": "evidence", "ref_id": first.id}
    async with isolated_db.session() as session:
        detail = await ExperienceService(session).get(created.experience_id)
    updated_first, unchanged_second = detail.evidence_items
    assert (updated_first.action, updated_first.result, updated_first.metrics) == (
        "新行动一",
        "新结果一",
        "新指标一",
    )
    assert unchanged_second.model_dump() == second.model_dump()


async def test_adapter_builds_one_evidence_collection_context(isolated_db) -> None:
    """EvidenceAdapter 为共享会话一次加载全部 Item 和各自 revision。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(ExperienceCreate(title="Agent"))
    async with isolated_db.session() as session:
        detail = await EvidenceService(session).create(
            created.experience_id,
            EvidenceCreate(action="搭建平台", result="完成上线", metrics="2 周"),
        )
    evidence = detail.evidence_items[0]
    expected_revision = next(
        state.revision
        for state in detail.field_states
        if state.key == "action" and state.ref_id == evidence.id
    )
    adapter = ExperienceAdapter()
    binding = await adapter.validate_binding(
        SubjectRef(type="experience", id=str(created.experience_id)),
        TargetRef(key="evidence", ref_id=None),
    )
    state = await adapter.parse_input(
        {
            "conversation_id": 10,
            "run_id": 10,
            "adapter": "ExperienceAdapter",
            "subject": binding.subject.model_dump(mode="json"),
            "target": binding.target.model_dump(mode="json"),
            "language": "zh",
            "run_kind": "user_turn",
            "tools_enabled": True,
            "messages": [],
            "pending_tool_results": [],
        }
    )
    assert [item["id"] for item in state.target_value] == [evidence.id]
    assert state.evidence_revisions == {str(evidence.id): expected_revision}


def test_graph_has_one_continue_without_tools_node() -> None:
    """Graph 不再重复加载上下文，所有 Tool 分支复用唯一续跑节点。"""
    adapter = ExperienceAdapter()
    runtime = AiChatRuntime(
        _UnusedModel(),  # type: ignore[arg-type]
        adapter.get_tool_handlers(),
        ToolLifecycle(RepositoryFactory()),
    )
    graph = adapter.build_graph(runtime)
    assert tuple(adapter.get_tool_handlers()) == ("content_change",)
    assert "load_context" not in graph.nodes
    assert ("prepare_turn", "agent_stream") in graph.edges
    assert list(graph.nodes).count("continue_without_tools") == 1


def test_adapter_state_and_tool_description_have_separate_roles() -> None:
    """经历输入使用专属类型，Tool 调用协议不再写死在系统 Prompt。"""
    state = ExperienceInputState(
        experience={},
        target_value=None,
        normalized_target_value=None,
        target_revision=0,
        target_status="incomplete",
        evidence_revisions={},
        system_prompt="测试",
        model_messages=[],
        tools_enabled=True,
    )
    handler = ContentChangeHandler()
    assert state.model_dump(mode="json")["target_revision"] == 0
    assert "suggested_content" in handler.description
    assert "content_change" not in system_prompt("zh", "background")


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
        assert any(event.event == "content_change.applied" for event in continued)
        assert sum(event.event == "assistant.completed" for event in continued) == 1
        async with isolated_db.session() as session:
            detail = await ExperienceService(session).get(created.experience_id)
        assert detail.background == "新背景"
    finally:
        await checkpoints.close()
