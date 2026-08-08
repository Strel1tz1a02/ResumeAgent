"""经历字段状态、Tool 和 Graph 的确定性业务测试。"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest
from sqlalchemy import create_engine

from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.checkpoint import CheckpointLifecycle
from app.ai_chat.graph.runner import GraphRunner
from app.ai_chat.adapters import AdapterRegistry
from app.ai_chat.services import AiChatService
from app.ai_chat.streaming.events import AiChatEvent
from app.ai_chat.streaming.compatibility import DsmlToolCallFallback
from app.ai_chat.streaming.model import ModelCompleted, TextDelta, ToolCallsCompleted
from app.ai_chat.tools.buffer import AssembledToolCall
from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.tools.handler import ToolContext
from app.ai_chat.tools.results import ApprovalProposal, ToolResult
from app.ai_chat.tools.lifecycle import ApprovalRequired, ToolCompleted, ToolLifecycle
from app.ai_chat.types import ScopeRef, SubjectRef
from app.database import Database
from app.experience import ExperienceAdapter
from app.experience.graph import ExperienceState, build_experience_graph
from app.experience.prompts.ai_chat import system_prompt
from app.experience.tools.content_change import (
    ContentChangeArguments,
    ContentChangeHandler,
)
from app.experience.schemas.evidence_items import EvidenceCreateRequest
from app.experience.schemas.experiences import ExperienceCreate, ExperienceUpdate
from app.experience.services.evidence_service import EvidenceService
from app.experience.services.experience_ai_mutation_service import ExperienceAiMutationService
from app.experience.services.experience_service import ExperienceConflictError, ExperienceService
from app.scripts.migrate_unified_experience_revision_units import (
    MIGRATION_NAME as UNIFIED_REVISION_MIGRATION,
    migrate as migrate_unified_revision_units,
)
from app.scripts.migrate_ai_chat_tool_call_index import (
    MIGRATION_NAME as AI_CHAT_TOOL_INDEX_MIGRATION,
    migrate as migrate_ai_chat_tool_call_index,
)
from app.scripts.migrate_ai_chat_conversation_scope import (
    MIGRATION_NAME as AI_CHAT_SCOPE_MIGRATION,
    migrate as migrate_ai_chat_conversation_scope,
)
from app.scripts.migrate_experience_chat_scope_field import (
    MIGRATION_NAME as EXPERIENCE_CHAT_SCOPE_FIELD_MIGRATION,
    migrate as migrate_experience_chat_scope_field,
)


class _UnusedModel:
    async def stream(self, **kwargs):  # type: ignore[no-untyped-def]
        if False:
            yield kwargs


class _ConversationModel:
    """生成确定性的经历内容修改提案。"""

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
                        "scope": {"field": "background", "evidence_id": None},
                        "suggested_content": "新背景",
                    },
                ),
            )
        )
        yield ModelCompleted("tool_calls")


class _ResultOnlyResumeRunner:
    """模拟业务 Graph 在审批恢复后只发出工具结果。"""

    async def resume(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["approval"]["decision"] == "reject"
        yield AiChatEvent("content_change.rejected", {"outcome": "rejected"})

    async def ensure_interrupted(self, **kwargs):  # type: ignore[no-untyped-def]
        return True


class _ImmediateToolRuntime:
    """让经历 Graph 收到一个无需审批的工具结果。"""

    async def stream_model(self, **kwargs):  # type: ignore[no-untyped-def]
        yield ToolCallsCompleted(
            (
                AssembledToolCall(
                    index=0,
                    provider_id="immediate-call",
                    name="content_change",
                    arguments={
                        "scope": {"field": "background", "evidence_id": None},
                        "suggested_content": "原值",
                    },
                ),
            )
        )

    async def receive_tool_call(self, **kwargs):  # type: ignore[no-untyped-def]
        return ToolCompleted(tool_call_id=7, result={"outcome": "no_change"})


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
        scope={"field": "background"},
        adapter_context={
            "revision_snapshot": {
                "scope": "field",
                "revision": state.revision,
            },
        },
    )
    handler = ContentChangeHandler()
    arguments = ContentChangeArguments(
        scope={"field": "background", "evidence_id": None},
        suggested_content="新背景",
    )
    proposal = await handler.invoke(
        context, arguments
    )
    assert isinstance(proposal, ApprovalProposal)
    assert proposal.proposal_payload["current_content"] == "旧背景"
    async with isolated_db.session() as session:
        result = await handler.resolve(
            replace(context, session=session),
            proposal.proposal_payload,
            proposal.guard_payload,
            "approve",
        )
        await session.commit()
    assert result.payload["outcome"] == "applied"
    assert result.payload["value"] == "新背景"


async def test_change_validation_is_owned_by_service(isolated_db) -> None:
    """模型 scope 与会话绑定不一致时由 Service 返回 invalid_scope。"""
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
            scope_field="background",
            expected_revision=state.revision,
        )
    assert result == {"outcome": "invalid_scope"}


async def test_content_change_routes_by_requested_scope(
    isolated_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tool 按模型提交的 scope 选服务，业务 Service 再校验会话范围。"""
    called: list[str] = []

    async def prepare_evidence_append(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        called.append("evidence_append")
        return {"outcome": "invalid_scope"}

    async def prepare_field_change(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        called.append("field_change")
        return {"outcome": "invalid_scope"}

    monkeypatch.setattr(
        ExperienceAiMutationService,
        "prepare_evidence_append",
        prepare_evidence_append,
    )
    monkeypatch.setattr(
        ExperienceAiMutationService,
        "prepare_field_change",
        prepare_field_change,
    )
    context = ToolContext(
        conversation_id=1,
        run_id=1,
        tool_call_id=1,
        subject={"type": "experience", "id": "1"},
        scope={"field": "background"},
        adapter_context={
            "revision_snapshot": {"scope": "field", "revision": 0},
        },
    )
    result = await ContentChangeHandler().invoke(
        context,
        ContentChangeArguments(
            scope={"field": "evidence", "evidence_id": None},
            suggested_content={"action": "行动", "result": None, "metrics": None},
        ),
    )
    assert isinstance(result, ToolResult)
    assert result.payload == {"outcome": "invalid_scope"}
    assert called == ["evidence_append"]


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
        scope={"field": "evidence"},
        adapter_context={
            "revision_snapshot": {
                "scope": "evidence",
                "collection_revision": collection.revision,
                "item_revisions": {},
            },
        },
    )
    arguments = ContentChangeArguments(
        scope={"field": "evidence", "evidence_id": None},
        suggested_content={
            "action": "搭建发布流水线",
            "result": "自动发布",
            "metrics": None,
        },
    )
    handler = ContentChangeHandler()
    proposal = await handler.invoke(context, arguments)
    assert isinstance(proposal, ApprovalProposal)
    async with isolated_db.session() as session:
        result = await handler.resolve(
            replace(context, session=session),
            proposal.proposal_payload,
            proposal.guard_payload,
            "approve",
        )
        await session.commit()
    assert result.payload["outcome"] == "applied"
    assert result.payload["evidence_ids"] == [result.payload["evidence_id"]]


async def test_content_change_overwrites_one_complete_evidence_item(isolated_db) -> None:
    """共享 Evidence 会话按 ID 整体覆盖一项，不修改其他 EvidenceItem。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(ExperienceCreate(title="Agent"))
    async with isolated_db.session() as session:
        await EvidenceService(session).create(
            created.experience_id,
            EvidenceCreateRequest(
                action="旧行动一",
                result="旧结果一",
                metrics="旧指标一",
                expected_collection_revision=0,
            ),
        )
    async with isolated_db.session() as session:
        with_both = await EvidenceService(session).create(
            created.experience_id,
            EvidenceCreateRequest(
                action="旧行动二",
                result="旧结果二",
                metrics="旧指标二",
                expected_collection_revision=1,
            ),
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
        scope={"field": "evidence"},
        adapter_context={
            "revision_snapshot": {
                "scope": "evidence",
                "collection_revision": 2,
                "item_revisions": {str(first.id): first_revision},
            },
        },
    )
    arguments = ContentChangeArguments(
        scope={"field": "evidence", "evidence_id": first.id},
        suggested_content={
            "action": "新行动一",
            "result": "新结果一",
            "metrics": "新指标一",
        },
    )
    handler = ContentChangeHandler()
    proposal = await handler.invoke(context, arguments)
    assert isinstance(proposal, ApprovalProposal)
    async with isolated_db.session() as session:
        result = await handler.resolve(
            replace(context, session=session),
            proposal.proposal_payload,
            proposal.guard_payload,
            "approve",
        )
        await session.commit()
    assert result.payload["scope"] == {
        "field": "evidence",
        "evidence_id": first.id,
    }
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
            EvidenceCreateRequest(
                action="搭建平台",
                result="完成上线",
                metrics="2 周",
                expected_collection_revision=0,
            ),
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
        ScopeRef.model_validate({"field": "evidence"}),
    )
    state = await adapter.parse_input(
        {
            "conversation_id": 10,
            "run_id": 10,
            "subject": binding.subject.model_dump(mode="json"),
            "scope": binding.scope.model_dump(mode="json"),
            "language": "zh",
            "run_kind": "user_turn",
            "tools_enabled": True,
            "messages": [],
            "pending_tool_results": [],
        }
    )
    assert f'"id": {evidence.id}' in str(state["model_messages"][1]["content"])
    assert state["revision_snapshot"] == {
        "scope": "evidence",
        "collection_revision": 1,
        "item_revisions": {str(evidence.id): expected_revision},
    }


def test_graph_has_only_llm_tool_executor_and_approver() -> None:
    """经历 Graph 只保留模型、工具执行和审批三个业务节点。"""
    adapter = ExperienceAdapter()
    runtime = AiChatRuntime(
        _UnusedModel(),  # type: ignore[arg-type]
        ToolLifecycle(RepositoryFactory()),
    ).bind_tools(adapter.get_tool_handlers())
    graph = adapter.build_graph(runtime)
    assert tuple(adapter.get_tool_handlers()) == ("content_change",)
    assert set(graph.nodes) == {"llm", "tool_executor", "approver"}
    assert ("__start__", "llm") in graph.edges
    assert ("approver", "__end__") in graph.edges


@pytest.mark.asyncio
async def test_graph_emits_immediate_tool_result() -> None:
    """无需审批的 Tool Result 在当前轮立即成为业务事件。"""
    graph = build_experience_graph(_ImmediateToolRuntime()).compile()  # type: ignore[arg-type]
    state = ExperienceState(
        conversation_id=1,
        run_id=1,
        subject={"type": "experience", "id": "1"},
        scope={"field": "background"},
        run_kind="user_turn",
        tools_enabled=True,
        revision_snapshot={"scope": "field", "revision": 0},
        model_messages=[],
        tool_call=None,
        proposal_id=None,
    )
    parts = [
        part
        async for part in graph.astream(
            state,
            stream_mode=["custom"],
            version="v2",
        )
    ]
    assert any(
        part.get("type") == "custom"
        and part.get("data", {}).get("event") == "content_change.no_change"
        and part["data"]["data"]["tool_call_id"] == 7
        for part in parts
    )


@pytest.mark.asyncio
async def test_generic_service_forwards_graph_result_without_assistant_continuation(
    isolated_db,
) -> None:
    """审批恢复只转发业务结果，不创建虚假的助手续答。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(
            ExperienceCreate(title="工具续答验证")
        )
    adapter = ExperienceAdapter()
    registry = AdapterRegistry()
    registry.register(adapter)
    repositories = RepositoryFactory()
    lifecycle = ToolLifecycle(repositories)
    service = AiChatService(
        registry,
        _ResultOnlyResumeRunner(),  # type: ignore[arg-type]
        lifecycle,
        repositories,
    )
    conversation_id = await service.create_conversation(
        "ExperienceAdapter",
        {"type": "experience", "id": str(created.experience_id)},
        {"field": "background"},
    )
    async with isolated_db.session() as session:
        repos = repositories.create(session)
        run = await repos.runs.create(
            conversation_id=conversation_id,
            kind="user_turn",
            tools_enabled=True,
        )
        assert await repos.runs.transition(
            run.id,
            from_statuses={"running"},
            to_status="suspended",
        )
        call = await repos.tool_calls.create(
            conversation_id=conversation_id,
            run_id=run.id,
            tool_call_index=0,
            provider_tool_call_id="future-tool-call",
            tool_name="content_change",
            arguments={
                "scope": {"field": "background", "evidence_id": None},
                "suggested_content": "不会被应用",
            },
        )
        await repos.tool_calls.request_approval(
            call,
            proposal_payload={"suggested_content": "不会被应用"},
            guard_payload={},
        )
        await session.commit()
        proposal_id = call.id

    events = [
        event
        async for event in service.resolve_proposal(
            proposal_id,
            "reject",
            "future-resolution",
        )
    ]
    assert [event.event for event in events] == [
        "proposal.resolved",
        "content_change.rejected",
    ]
    async with isolated_db.session() as session:
        repos = repositories.create(session)
        resolved = await repos.tool_calls.get(proposal_id)
        finished_run = await repos.runs.get(run.id)
        messages = await repos.messages.list_completed(conversation_id)
        assert resolved is not None
        assert resolved.delivery_status == "pending"
        assert finished_run is not None
        assert finished_run.status == "completed"
        assert messages == []


def test_experience_state_and_tool_description_have_separate_roles() -> None:
    """经历 Graph 使用完整业务 State，Tool 协议不写死在系统 Prompt。"""
    state = ExperienceState(
        conversation_id=1,
        run_id=1,
        subject={"type": "experience", "id": "1"},
        scope={"field": "background"},
        run_kind="user_turn",
        tools_enabled=True,
        revision_snapshot={"scope": "field", "revision": 0},
        model_messages=[],
        tool_call=None,
        proposal_id=None,
    )
    handler = ContentChangeHandler()
    assert state["revision_snapshot"]["revision"] == 0
    assert "suggested_content" in handler.description
    assert "content_change" not in system_prompt("zh", "background")


def test_dsml_compatibility_recovers_atomic_tool_call() -> None:
    """提供方泄漏的 DSML 被隔离解析，正文中不展示协议标签。"""
    fallback = DsmlToolCallFallback()
    visible = fallback.feed("说明<｜｜DSML｜｜tool_calls>")
    visible += fallback.feed(
        '<｜｜DSML｜｜invoke name="content_change">'
        '<｜｜DSML｜｜parameter name="scope" string="false">'
        '{"field":"background","evidence_id":null}'
        '</｜｜DSML｜｜parameter>'
        '<｜｜DSML｜｜parameter name="suggested_content" string="true">新背景'
        '</｜｜DSML｜｜parameter>'
        '</｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>结束'
    )
    calls, trailing = fallback.finish()

    assert visible + trailing == "说明结束"
    assert len(calls) == 1
    assert calls[0].provider_id is None
    assert calls[0].name == "content_change"
    assert calls[0].arguments == {
        "scope": {"field": "background", "evidence_id": None},
        "suggested_content": "新背景",
    }


async def test_tool_call_run_index_is_idempotent_without_provider_id(
    isolated_db,
) -> None:
    """无 provider ID 的兼容调用也能重放，并且 resolved 优先于旧提案。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(
            ExperienceCreate(title="Tool 重放", background="旧背景")
        )
        repositories = RepositoryFactory().create(session)
        conversation = await repositories.conversations.create(
            adapter="ExperienceAdapter",
            subject={"type": "experience", "id": str(created.experience_id)},
            scope={"field": "background"},
            language="zh",
        )
        run = await repositories.runs.create(
            conversation_id=conversation.id,
            kind="user_turn",
            tools_enabled=True,
        )
        await session.commit()

    handler = ContentChangeHandler()
    handlers = {handler.name: handler}
    lifecycle = ToolLifecycle(RepositoryFactory())
    call = AssembledToolCall(
        index=0,
        provider_id=None,
        name="content_change",
        arguments={
            "scope": {"field": "background", "evidence_id": None},
            "suggested_content": "新背景",
        },
    )
    receive_kwargs = {
        "context": ToolContext(
            conversation_id=conversation.id,
            run_id=run.id,
            subject={"type": "experience", "id": str(created.experience_id)},
            scope={"field": "background"},
            adapter_context={
                "revision_snapshot": {"scope": "field", "revision": 0}
            },
        ),
        "call": call,
        "handlers": handlers,
    }

    first = await lifecycle.receive(**receive_kwargs)
    replay = await lifecycle.receive(**receive_kwargs)
    provider_replay = await lifecycle.receive(
        **{**receive_kwargs, "call": replace(call, provider_id="retry-generated-id")}
    )
    assert isinstance(first, ApprovalRequired)
    assert isinstance(replay, ApprovalRequired)
    assert isinstance(provider_replay, ApprovalRequired)
    assert replay.tool_call_id == first.tool_call_id
    assert provider_replay.tool_call_id == first.tool_call_id

    result = await lifecycle.resolve(
        tool_call_id=first.tool_call_id,
        decision="reject",
        handlers=handlers,
        subject=receive_kwargs["context"].subject,
        scope=receive_kwargs["context"].scope,
        client_resolution_id="resolution-without-provider-id",
    )
    assert result["outcome"] == "rejected"

    resolved_replay = await lifecycle.receive(**receive_kwargs)
    assert isinstance(resolved_replay, ToolCompleted)
    assert resolved_replay.tool_call_id == first.tool_call_id
    assert resolved_replay.result["outcome"] == "rejected"


def test_migration_moves_ordered_evidence_ids_and_drops_legacy_columns(tmp_path) -> None:
    """旧库 JSON 关系按顺序迁移，重复与悬空 ID 不进入关系表。"""
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
            "CREATE TABLE evidence_items ("
            "id INTEGER PRIMARY KEY, action TEXT NOT NULL, result TEXT, metrics TEXT, "
            "created_at VARCHAR NOT NULL, updated_at VARCHAR NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO evidence_items VALUES (?,?,?,?,?,?)",
            [
                (1, "First", None, None, "now", "now"),
                (2, "Second", None, None, "now", "now"),
            ],
        )
        connection.execute(
            "INSERT INTO experience_items VALUES "
            "(1,'project','Agent',NULL,NULL,NULL,NULL,NULL,0,'必须销毁',NULL,'[2,1,2,999]','[]','[]',NULL,'draft',0,NULL,'now','now')"
        )
    database = Database(path)
    with database._sync() as session:  # noqa: SLF001 - migration verification
        columns = session.connection().exec_driver_sql(
            "PRAGMA table_info(experience_items)"
        ).mappings().all()
        state_columns = session.connection().exec_driver_sql(
            "PRAGMA table_info(experience_field_states)"
        ).mappings().all()
        migrations = set(
            session.connection()
            .exec_driver_sql("SELECT name FROM schema_migrations")
            .scalars()
            .all()
        )
        evidence_links = session.connection().exec_driver_sql(
            "SELECT experience_id, evidence_id, position "
            "FROM experience_evidence_items ORDER BY position"
        ).all()
        revision_rows = set(
            session.connection().exec_driver_sql(
                "SELECT scope, unit_key, ref_id, revision FROM experience_revisions"
            ).all()
        )
    assert "raw_input" not in {column["name"] for column in columns}
    assert "evidence_ids" not in {column["name"] for column in columns}
    assert "revision" not in {column["name"] for column in state_columns}
    assert migrations == {
        "2026_08_01_experience_field_states",
        "2026_08_03_experience_evidence_items",
        "2026_08_04_experience_revisions",
        "2026_08_05_unified_experience_revision_units",
        "2026_08_07_ai_chat_tool_call_index",
        "2026_08_08_ai_chat_conversation_scope",
        "2026_08_08_experience_chat_scope_field",
    }
    assert evidence_links == [(1, 2, 0), (1, 1, 1)]
    assert {
        ("unit", "identity", 0, 0),
        ("unit", "dates", 0, 0),
        ("collection", "evidence", 0, 0),
        ("unit", "evidence", 1, 0),
        ("unit", "evidence", 2, 0),
    } <= revision_rows


def test_migration_unifies_existing_save_unit_and_evidence_scopes(tmp_path) -> None:
    """已经运行过旧版本的库会把两种重叠 scope 原样迁入 unit。"""
    path = tmp_path / "legacy-revisions.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE experience_items (experience_id INTEGER PRIMARY KEY)"
        )
        connection.execute("INSERT INTO experience_items VALUES (1)")
        connection.execute(
            "CREATE TABLE experience_revisions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, experience_id INTEGER NOT NULL, "
            "scope VARCHAR(16) NOT NULL CHECK (scope IN ('save_unit','evidence','collection')), "
            "unit_key VARCHAR(80) NOT NULL, ref_id INTEGER NOT NULL DEFAULT 0, "
            "revision INTEGER NOT NULL DEFAULT 0, created_at VARCHAR NOT NULL, updated_at VARCHAR NOT NULL, "
            "UNIQUE (experience_id, scope, unit_key, ref_id))"
        )
        connection.executemany(
            "INSERT INTO experience_revisions "
            "(experience_id,scope,unit_key,ref_id,revision,created_at,updated_at) "
            "VALUES (?,?,?,?,?,'before','after')",
            [
                (1, "save_unit", "identity", 0, 2),
                (1, "evidence", "evidence", 7, 3),
                (1, "collection", "evidence", 0, 4),
            ],
        )

    engine = create_engine(f"sqlite:///{path}")
    try:
        migrate_unified_revision_units(engine)
        with engine.connect() as connection:
            rows = set(
                connection.exec_driver_sql(
                    "SELECT scope,unit_key,ref_id,revision FROM experience_revisions"
                ).all()
            )
            migration = connection.exec_driver_sql(
                "SELECT name FROM schema_migrations WHERE name = ?",
                (UNIFIED_REVISION_MIGRATION,),
            ).scalar_one()
    finally:
        engine.dispose()

    assert rows == {
        ("unit", "identity", 0, 2),
        ("unit", "evidence", 7, 3),
        ("collection", "evidence", 0, 4),
    }
    assert migration == UNIFIED_REVISION_MIGRATION


def test_migration_backfills_ai_chat_tool_call_index(tmp_path) -> None:
    """旧 Tool Call 按 Run 内写入顺序获得稳定且唯一的索引。"""
    path = tmp_path / "legacy-ai-chat-tools.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE ai_chat_tool_calls ("
            "id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO ai_chat_tool_calls (id, run_id) VALUES (?, ?)",
            [(10, 7), (12, 8), (11, 7)],
        )

    engine = create_engine(f"sqlite:///{path}")
    try:
        migrate_ai_chat_tool_call_index(engine)
        with engine.connect() as connection:
            rows = connection.exec_driver_sql(
                "SELECT id, run_id, tool_call_index "
                "FROM ai_chat_tool_calls ORDER BY run_id, id"
            ).all()
            migration = connection.exec_driver_sql(
                "SELECT name FROM schema_migrations WHERE name = ?",
                (AI_CHAT_TOOL_INDEX_MIGRATION,),
            ).scalar_one()
            indexes = {
                row[1]: bool(row[2])
                for row in connection.exec_driver_sql(
                    "PRAGMA index_list(ai_chat_tool_calls)"
                ).all()
            }
    finally:
        engine.dispose()

    assert rows == [(10, 7, 0), (11, 7, 1), (12, 8, 0)]
    assert indexes["ux_ai_chat_tool_run_index"] is True
    assert migration == AI_CHAT_TOOL_INDEX_MIGRATION


def test_migration_renames_ai_chat_conversation_target_to_scope(tmp_path) -> None:
    """旧会话数据原样迁移到语义统一的 scope 列。"""
    path = tmp_path / "legacy-ai-chat-conversations.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE ai_chat_conversations ("
            "id INTEGER PRIMARY KEY, target JSON NOT NULL)"
        )
        connection.execute(
            "INSERT INTO ai_chat_conversations (id, target) VALUES (?, ?)",
            (1, '{"key":"background","ref_id":null}'),
        )

    engine = create_engine(f"sqlite:///{path}")
    try:
        migrate_ai_chat_conversation_scope(engine)
        with engine.connect() as connection:
            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(ai_chat_conversations)"
                ).all()
            }
            payload = connection.exec_driver_sql(
                "SELECT scope FROM ai_chat_conversations WHERE id = 1"
            ).scalar_one()
            migration = connection.exec_driver_sql(
                "SELECT name FROM schema_migrations WHERE name = ?",
                (AI_CHAT_SCOPE_MIGRATION,),
            ).scalar_one()
    finally:
        engine.dispose()

    assert "scope" in columns
    assert "target" not in columns
    assert payload == '{"key":"background","ref_id":null}'
    assert migration == AI_CHAT_SCOPE_MIGRATION


def test_migration_normalizes_experience_chat_scope_to_field(tmp_path) -> None:
    """旧经历会话只保留其会话字段，不再保存无效 ref_id。"""
    path = tmp_path / "legacy-experience-chat-scope.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE ai_chat_conversations ("
            "id INTEGER PRIMARY KEY, adapter VARCHAR NOT NULL, scope JSON NOT NULL)"
        )
        connection.execute(
            "INSERT INTO ai_chat_conversations (id, adapter, scope) VALUES (?, ?, ?)",
            (
                1,
                "ExperienceAdapter",
                '{"key":"evidence","ref_id":null}',
            ),
        )

    engine = create_engine(f"sqlite:///{path}")
    try:
        migrate_experience_chat_scope_field(engine)
        with engine.connect() as connection:
            payload = connection.exec_driver_sql(
                "SELECT scope FROM ai_chat_conversations WHERE id = 1"
            ).scalar_one()
            migration = connection.exec_driver_sql(
                "SELECT name FROM schema_migrations WHERE name = ?",
                (EXPERIENCE_CHAT_SCOPE_FIELD_MIGRATION,),
            ).scalar_one()
    finally:
        engine.dispose()

    assert payload == '{"field":"evidence"}'
    assert migration == EXPERIENCE_CHAT_SCOPE_FIELD_MIGRATION


async def test_real_graph_interrupt_approve_and_deferred_tool_result(
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
    runtime = AiChatRuntime(_ConversationModel(), lifecycle)  # type: ignore[arg-type]
    runner = GraphRunner(registry, saver, runtime)
    service = AiChatService(registry, runner, lifecycle, repositories)
    try:
        conversation_id = await service.create_conversation(
            "ExperienceAdapter",
            {"type": "experience", "id": str(created.experience_id)},
            {"field": "background"},
        )
        stream = service.stream_message(conversation_id, "请改写背景", "message-1")
        events = []
        async for event in stream:
            events.append(event)
            if event.event == "proposal.requested":
                break
        await stream.aclose()
        proposal = next(event for event in events if event.event == "proposal.requested")
        proposal_id = int(proposal.data["proposal_id"])
        async with isolated_db.session() as session:
            current_run = await RepositoryFactory().create(session).runs.current(
                conversation_id
            )
        assert current_run is not None
        assert current_run.status == "suspended"
        # 兼容修复前“前端先断流”遗留的 cancelled/running 提案。
        async with isolated_db.session() as session:
            repositories = RepositoryFactory().create(session)
            stale_run = await repositories.runs.get(current_run.id)
            assert stale_run is not None
            assert await repositories.runs.transition(
                stale_run.id,
                from_statuses={"suspended"},
                to_status="cancelled",
            )
            await session.commit()
        # 模拟业务覆盖已提交，但进程在创建 continuation 前退出。重试审批必须
        # 复用已持久化 Tool Result，并恢复原 Run，而不能重复覆盖字段。
        resolved_before_continuation = await lifecycle.resolve(
            tool_call_id=proposal_id,
            decision="approve",
            handlers=adapter.get_tool_handlers(),
            subject={"type": "experience", "id": str(created.experience_id)},
            scope={"field": "background"},
            client_resolution_id="resolution-1",
        )
        assert resolved_before_continuation["outcome"] == "applied"
        continued = [
            event
            async for event in service.resolve_proposal(
                proposal_id, "approve", "resolution-1"
            )
        ]
        assert any(event.event == "content_change.applied" for event in continued)
        assert not any(event.event.startswith("assistant.") for event in continued)
        async with isolated_db.session() as session:
            detail = await ExperienceService(session).get(created.experience_id)
        assert detail.background == "新背景"
        background = next(
            state for state in detail.field_states if state.key == "background"
        )
        assert background.revision == 1
        async with isolated_db.session() as session:
            repositories = RepositoryFactory().create(session)
            original = await repositories.runs.get(current_run.id)
            assert original is not None
            assert original.status == "completed"
            resolved_call = await repositories.tool_calls.get(proposal_id)
            assert resolved_call is not None
            assert resolved_call.delivery_status == "pending"
            assert not await repositories.runs.transition(
                original.id,
                from_statuses={"suspended"},
                to_status="cancelled",
            )
            await session.rollback()
        follow_up = [
            event
            async for event in service.stream_message(
                conversation_id, "继续", "message-2"
            )
        ]
        assert any(event.event == "assistant.completed" for event in follow_up)
        async with isolated_db.session() as session:
            delivered_call = await RepositoryFactory().create(session).tool_calls.get(
                proposal_id
            )
            assert delivered_call is not None
            assert delivered_call.delivery_status == "consumed"
    finally:
        await checkpoints.close()
