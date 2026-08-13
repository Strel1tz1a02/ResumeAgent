"""经历字段状态、工具和图的确定性业务测试。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NotRequired, get_origin, get_type_hints

import pytest
from app.ai_chat.adapters import AdapterRegistry
from app.ai_chat.checkpoint import CheckpointLifecycle
from app.ai_chat.errors import (
    IdempotencyConflictError,
    ProposalStateError,
    RunInProgressError,
    ToolProtocolError,
)
from app.ai_chat.graph.runner import GraphRecovery, GraphRunner
from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.repositories.run_repository import RunRepository
from app.ai_chat.services import AiChatService, ToolCallService
from app.ai_chat.streaming.compatibility import DsmlToolCallFallback
from app.ai_chat.streaming.events import AiChatEvent, tool_result_event
from app.ai_chat.streaming.model import ModelCompleted, TextDelta, ToolCallsCompleted
from app.ai_chat.tools.buffer import encode_tool_call
from app.ai_chat.tools.security import ToolSecurity, guard_tool
from app.ai_chat.tools.types import (
    ToolContext,
    ToolResult,
)
from app.ai_chat.types import ScopeRef, SubjectRef
from app.database import Database
from app.experience import ExperienceAdapter
from app.experience.graph import ExperienceState, build_experience_graph
from app.experience.graph.builder import _durable_call
from app.experience.prompts.ai_chat import system_prompt
from app.experience.schemas.evidence_items import EvidenceCreateRequest
from app.experience.schemas.experiences import ExperienceCreate, ExperienceUpdate
from app.experience.services.evidence_service import EvidenceService
from app.experience.services.experience_ai_mutation_service import (
    ExperienceAiMutationService,
)
from app.experience.services.experience_service import (
    ExperienceConflictError,
    ExperienceService,
)
from app.experience.tools.content_change import (
    ContentChangeArguments,
    ContentChangeHandler,
)
from app.scripts.migrate_ai_chat_conversation_scope import (
    MIGRATION_NAME as AI_CHAT_SCOPE_MIGRATION,
)
from app.scripts.migrate_ai_chat_conversation_scope import (
    migrate as migrate_ai_chat_conversation_scope,
)
from app.scripts.migrate_ai_chat_tool_call_index import (
    MIGRATION_NAME as AI_CHAT_TOOL_INDEX_MIGRATION,
)
from app.scripts.migrate_ai_chat_tool_call_index import (
    migrate as migrate_ai_chat_tool_call_index,
)
from app.scripts.migrate_experience_chat_scope_field import (
    MIGRATION_NAME as EXPERIENCE_CHAT_SCOPE_FIELD_MIGRATION,
)
from app.scripts.migrate_experience_chat_scope_field import (
    migrate as migrate_experience_chat_scope_field,
)
from app.scripts.migrate_unified_experience_revision_units import (
    MIGRATION_NAME as UNIFIED_REVISION_MIGRATION,
)
from app.scripts.migrate_unified_experience_revision_units import (
    migrate as migrate_unified_revision_units,
)
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession


class _UnusedModel:
    async def stream(self, **kwargs):  # type: ignore[no-untyped-def]
        if False:
            yield kwargs


class _PassthroughMemoryService:
    async def get_history_prompt(self, _run_id, _occupied_token):  # type: ignore[no-untyped-def]
        return '{"memory":{},"runs":[]}'


class _ConversationModel:
    """生成确定性的经历内容修改提案。"""

    def __init__(self, provider_id: str = "call-1") -> None:
        self._provider_id = provider_id

    async def stream(self, *, tools_enabled: bool, **kwargs):  # type: ignore[no-untyped-def]
        if not tools_enabled:
            yield TextDelta("已处理")
            yield ModelCompleted("stop")
            return
        yield ToolCallsCompleted(
            (
                encode_tool_call(
                    index=0,
                    provider_id=self._provider_id,
                    name="content_change",
                    arguments=json.dumps(
                        {
                            "scope": {
                                "field": "background",
                                "evidence_id": None,
                            },
                            "suggested_content": "新背景",
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        )
        yield ModelCompleted("tool_calls")


class _LowRiskContentChangeHandler(ContentChangeHandler):
    """只用于验证 guard 的直接执行分支。"""

    security = ToolSecurity.LOW


class _FailingContentChangeHandler(ContentChangeHandler):
    """记录真实 Handler 入口，并按配置制造 executor 瞬时失败。"""

    def __init__(self, failures: int) -> None:
        self._failures = failures
        self.execute_count = 0

    async def execute(self, context, proposal_payload, guard_payload):  # type: ignore[no-untyped-def]
        self.execute_count += 1
        if self.execute_count <= self._failures:
            raise RuntimeError("transient executor failure")
        return await super().execute(context, proposal_payload, guard_payload)


class _BlockingContentChangeHandler(ContentChangeHandler):
    """让真实 executor 停在可取消边界。"""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.execute_count = 0

    async def execute(self, context, proposal_payload, guard_payload):  # type: ignore[no-untyped-def]
        self.execute_count += 1
        self.entered.set()
        await self.release.wait()
        return await super().execute(context, proposal_payload, guard_payload)


class _ProposalStateFailingHandler(ContentChangeHandler):
    """模拟 Graph 已启动后的稳定协议错误。"""

    def __init__(self) -> None:
        self.execute_count = 0

    async def execute(self, context, proposal_payload, guard_payload):  # type: ignore[no-untyped-def]
        self.execute_count += 1
        raise ProposalStateError("executor checkpoint state is invalid")


@dataclass(frozen=True)
class _GraphHarness:
    """真实数据库、检查点器与图的测试装配。"""

    experience_id: int
    adapter: ExperienceAdapter
    repositories: RepositoryFactory
    checkpoints: CheckpointLifecycle
    runner: GraphRunner
    service: AiChatService
    conversation_id: int


async def _start_graph_harness(
    isolated_db,
    checkpoint_path: Path,
    *,
    handler: ContentChangeHandler | None = None,
) -> _GraphHarness:
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(
            ExperienceCreate(title="恢复验证", background="旧背景")
        )
    adapter = ExperienceAdapter()
    if handler is not None:
        adapter._handlers = {handler.name: handler}
    registry = AdapterRegistry()
    registry.register(adapter)
    repositories = RepositoryFactory()
    checkpoints = CheckpointLifecycle(checkpoint_path)
    saver = await checkpoints.start()
    runtime = AiChatRuntime(
        _ConversationModel(),  # type: ignore[arg-type]
        ToolCallService(isolated_db.session, repositories),
        _PassthroughMemoryService(),  # type: ignore[arg-type]
    )
    runner = GraphRunner(registry, saver, runtime)
    service = AiChatService(registry, runner, repositories)
    conversation_id = await service.create_conversation(
        "ExperienceAdapter",
        {"type": "experience", "id": str(created.experience_id)},
        {"field": "background"},
    )
    return _GraphHarness(
        experience_id=created.experience_id,
        adapter=adapter,
        repositories=repositories,
        checkpoints=checkpoints,
        runner=runner,
        service=service,
        conversation_id=conversation_id,
    )


async def _request_proposal(harness: _GraphHarness, message_id: str) -> int:
    events = [
        event
        async for event in harness.service.stream_message(
            harness.conversation_id,
            "请改写背景",
            message_id,
        )
    ]
    proposal = next(event for event in events if event.event == "proposal.requested")
    return int(proposal.data["proposal_id"])


class _ResultOnlyResumeRunner:
    """模拟业务 Graph 在审批恢复后只发出工具结果。"""

    async def resume(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["approval"]["decision"] == "reject"
        assert kwargs["approval"]["client_resolution_id"] == "future-resolution"
        yield AiChatEvent(
            "proposal.resolved",
            {"proposal_id": kwargs["approval"]["tool_call_id"], "decision": "reject"},
        )
        yield AiChatEvent("content_change.rejected", {"outcome": "rejected"})

    async def ensure_interrupted(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["approval"]["decision"] == "reject"
        return GraphRecovery(interrupted=True)


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
            ExperienceUpdate(title="新标题", expected_field_revisions={"title": 0}),
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


async def test_content_change_routes_field_proposal_and_apply(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    async with isolated_db.session() as session:
        proposal = await handler.validation(
            replace(context, session=session),
            arguments.model_dump(mode="json"),
        )
    assert isinstance(proposal, tuple)
    proposal_payload, guard_payload = proposal
    assert proposal_payload["current_content"] == "旧背景"
    shown: list[dict] = []
    original_show_result = handler.show_result

    def show_result(payload):  # type: ignore[no-untyped-def]
        shown.append(dict(payload))
        return original_show_result(payload)

    monkeypatch.setattr(handler, "show_result", show_result)
    async with isolated_db.session() as session:
        result = await handler.execute(
            replace(context, session=session),
            proposal_payload,
            guard_payload,
        )
        await session.commit()
    assert result.payload["outcome"] == "applied"
    assert result.payload["value"] == "新背景"
    assert shown == [result.payload]


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


async def test_handler_validation_rejects_invalid_model_arguments() -> None:
    """模型参数由目标 Handler 自己解析，未知字段不能进入 Graph guard。"""
    context = ToolContext(
        conversation_id=1,
        run_id=1,
        subject={"type": "experience", "id": "1"},
        scope={"field": "background"},
        adapter_context={
            "revision_snapshot": {"scope": "field", "revision": 0},
        },
    )
    with pytest.raises(ToolProtocolError):
        await ContentChangeHandler().validation(
            context,
            {
                "scope": {"field": "background", "evidence_id": None},
                "suggested_content": "新背景",
                "unexpected": True,
            },
        )


async def test_handler_validation_requires_shared_session(isolated_db) -> None:
    """合法模型参数也不能绕过 ToolCallService 注入的共享事务。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(
            ExperienceCreate(title="共享事务", background="旧背景")
        )
    state = next(item for item in created.field_states if item.key == "background")
    context = ToolContext(
        conversation_id=1,
        run_id=1,
        subject={"type": "experience", "id": str(created.experience_id)},
        scope={"field": "background"},
        adapter_context={
            "revision_snapshot": {"scope": "field", "revision": state.revision}
        },
    )

    with pytest.raises(
        RuntimeError,
        match="tool validation requires a shared transaction",
    ):
        await ContentChangeHandler().validation(
            context,
            {
                "scope": {"field": "background", "evidence_id": None},
                "suggested_content": "新背景",
            },
        )


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
    async with isolated_db.session() as session:
        result = await ContentChangeHandler().validation(
            replace(context, session=session),
            ContentChangeArguments(
                scope={"field": "evidence", "evidence_id": None},
                suggested_content={
                    "background": None,
                    "action": "行动",
                    "result": None,
                },
            ).model_dump(mode="json"),
        )
    assert isinstance(result, ToolResult)
    assert result.payload == {"outcome": "invalid_scope"}
    assert called == ["evidence_append"]


async def test_content_change_routes_evidence_append(isolated_db) -> None:
    """统一 Tool 将新增 Evidence 建议路由到追加 Service。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(
            ExperienceCreate(title="Agent")
        )
    collection = next(
        item for item in created.field_states if item.key == "evidence_new"
    )
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
            "background": None,
            "action": "搭建发布流水线",
            "result": "自动发布",
        },
    )
    handler = ContentChangeHandler()
    async with isolated_db.session() as session:
        proposal = await handler.validation(
            replace(context, session=session),
            arguments.model_dump(mode="json"),
        )
    assert isinstance(proposal, tuple)
    proposal_payload, guard_payload = proposal
    async with isolated_db.session() as session:
        result = await handler.execute(
            replace(context, session=session),
            proposal_payload,
            guard_payload,
        )
        await session.commit()
    assert result.payload["outcome"] == "applied"
    assert result.payload["evidence_ids"] == [result.payload["evidence_id"]]


async def test_content_change_overwrites_one_complete_evidence_item(
    isolated_db,
) -> None:
    """共享 Evidence 会话按 ID 整体覆盖一项，不修改其他 EvidenceItem。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(
            ExperienceCreate(title="Agent")
        )
    async with isolated_db.session() as session:
        await EvidenceService(session).create(
            created.experience_id,
            EvidenceCreateRequest(
                background="旧背景一",
                action="旧行动一",
                result="旧结果一",
                expected_collection_revision=0,
            ),
        )
    async with isolated_db.session() as session:
        with_both = await EvidenceService(session).create(
            created.experience_id,
            EvidenceCreateRequest(
                background="旧背景二",
                action="旧行动二",
                result="旧结果二",
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
            "background": "新背景一",
            "action": "新行动一",
            "result": "新结果一",
        },
    )
    handler = ContentChangeHandler()
    async with isolated_db.session() as session:
        proposal = await handler.validation(
            replace(context, session=session),
            arguments.model_dump(mode="json"),
        )
    assert isinstance(proposal, tuple)
    proposal_payload, guard_payload = proposal
    async with isolated_db.session() as session:
        result = await handler.execute(
            replace(context, session=session),
            proposal_payload,
            guard_payload,
        )
        await session.commit()
    assert result.payload["scope"] == {
        "field": "evidence",
        "evidence_id": first.id,
    }
    async with isolated_db.session() as session:
        detail = await ExperienceService(session).get(created.experience_id)
    updated_first, unchanged_second = detail.evidence_items
    assert (updated_first.background, updated_first.action, updated_first.result) == (
        "新背景一",
        "新行动一",
        "新结果一",
    )
    assert unchanged_second.model_dump() == second.model_dump()


async def test_adapter_builds_one_evidence_collection_context(isolated_db) -> None:
    """EvidenceAdapter 为共享会话一次加载全部 Item 和各自 revision。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(
            ExperienceCreate(title="Agent")
        )
    async with isolated_db.session() as session:
        detail = await EvidenceService(session).create(
            created.experience_id,
            EvidenceCreateRequest(
                background="需要缩短交付周期",
                action="搭建平台",
                result="2 周完成上线",
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


async def test_graph_separates_validator_guard_approval_and_executor(
    isolated_db,
) -> None:
    """经历 Graph 将校验、风险分流、人工暂停和执行分开。"""
    adapter = ExperienceAdapter()
    runtime = AiChatRuntime(
        _UnusedModel(),  # type: ignore[arg-type]
        ToolCallService(isolated_db.session, RepositoryFactory()),
        _PassthroughMemoryService(),  # type: ignore[arg-type]
    ).bind_tools(adapter.get_tool_handlers())
    graph = adapter.build_graph(runtime)
    assert tuple(adapter.get_tool_handlers()) == ("content_change",)
    assert set(graph.nodes) == {
        "llm",
        "validator",
        "guard",
        "approver",
        "executor",
    }
    assert ("__start__", "llm") in graph.edges
    assert ("executor", "__end__") in graph.edges


def test_experience_graph_delegates_tool_state_to_tool_service() -> None:
    """Graph 只编排节点，不自行持久化或调用 Handler 生产方法。"""
    source = (
        Path(__file__).parents[2] / "app" / "experience" / "graph" / "builder.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "runtime.repositories",
        "handler.validation",
        "handler.execute",
    ):
        assert forbidden not in source


def test_guard_owns_security_routing() -> None:
    """Handler 只声明风险，guard 统一决定是否人工审批。"""
    assert guard_tool(ToolSecurity.LOW) == "execute"
    assert guard_tool(ToolSecurity.MEDIUM) == "approval"
    assert guard_tool(ToolSecurity.HIGH) == "approval"


async def test_recovery_does_not_reuse_low_risk_route_after_security_increase(
    isolated_db,
) -> None:
    """处理器风险升高后，旧低风险检查点必须重新进入审批。"""
    async with isolated_db.session() as session:
        repositories = RepositoryFactory().create(session)
        conversation = await repositories.conversations.create(
            adapter="ExperienceAdapter",
            subject={"type": "experience", "id": "1"},
            scope={"field": "background"},
            language="zh",
        )
        run = await repositories.runs.create(
            conversation_id=conversation.id,
            kind="user_turn",
            tools_enabled=True,
        )
        row = await repositories.tool_calls.create(
            conversation_id=conversation.id,
            run_id=run.id,
            tool_call_index=0,
            provider_tool_call_id="security-drift",
            tool_name="content_change",
            arguments={},
        )
        assert await repositories.tool_calls.save_validation(
            row,
            proposal_payload={"suggested_content": "新背景"},
            guard_payload={"trusted": True},
        )
        await session.commit()

    low_service = ToolCallService(
        isolated_db.session,
        RepositoryFactory(),
    ).bind_handlers({"content_change": _LowRiskContentChangeHandler()})
    checkpoint_call = await low_service.get_call(row.id)
    checkpoint_call["should_execute"] = True
    medium_runtime = AiChatRuntime(
        _UnusedModel(),  # type: ignore[arg-type]
        ToolCallService(isolated_db.session, RepositoryFactory()),
        _PassthroughMemoryService(),  # type: ignore[arg-type]
    ).bind_tools({"content_change": ContentChangeHandler()})
    state = ExperienceState(
        conversation_id=conversation.id,
        run_id=run.id,
        subject={"type": "experience", "id": "1"},
        scope={"field": "background"},
        run_kind="user_turn",
        tools_enabled=True,
        revision_snapshot={},
        model_messages=[],
        raw_tool_call=None,
        tool_call=checkpoint_call,
    )

    restored = await _durable_call(state, medium_runtime)

    assert restored["security"] == ToolSecurity.MEDIUM.value
    assert restored["should_execute"] is None


async def test_low_security_tool_executes_without_approval(isolated_db) -> None:
    """LOW Tool 经 guard 直达 executor，且仍持久化稳定结果。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(
            ExperienceCreate(title="低风险工具", background="旧背景")
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
    revision = next(
        item.revision for item in created.field_states if item.key == "background"
    )
    handler = _LowRiskContentChangeHandler()
    runtime = AiChatRuntime(
        _ConversationModel(),  # type: ignore[arg-type]
        ToolCallService(isolated_db.session, RepositoryFactory()),
        _PassthroughMemoryService(),  # type: ignore[arg-type]
    ).bind_tools({handler.name: handler})
    graph = build_experience_graph(runtime).compile()
    parts = [
        part
        async for part in graph.astream(
            ExperienceState(
                conversation_id=conversation.id,
                run_id=run.id,
                subject={
                    "type": "experience",
                    "id": str(created.experience_id),
                },
                scope={"field": "background"},
                run_kind="user_turn",
                tools_enabled=True,
                revision_snapshot={"scope": "field", "revision": revision},
                model_messages=[],
                tool_call=None,
                approval=None,
            ),
            stream_mode=["custom", "updates"],
            version="v2",
        )
    ]
    events = [part["data"]["event"] for part in parts if part.get("type") == "custom"]
    assert "proposal.requested" not in events
    assert "content_change.applied" in events
    async with isolated_db.session() as session:
        detail = await ExperienceService(session).get(created.experience_id)
        row = (
            await RepositoryFactory()
            .create(session)
            .tool_calls.get_by_run_index(run.id, 0)
        )
    assert detail.background == "新背景"
    assert row is not None
    assert row.status == "resolved"
    assert row.decision is None


@pytest.mark.asyncio
async def test_graph_emits_validation_terminal_result(isolated_db) -> None:
    """validation 发现无变化时不进入审批或 executor。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(
            ExperienceCreate(title="立即结果", background="新背景")
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
    revision = next(
        item.revision for item in created.field_states if item.key == "background"
    )
    adapter = ExperienceAdapter()
    runtime = AiChatRuntime(
        _ConversationModel(),  # type: ignore[arg-type]
        ToolCallService(isolated_db.session, RepositoryFactory()),
        _PassthroughMemoryService(),  # type: ignore[arg-type]
    ).bind_tools(adapter.get_tool_handlers())
    graph = build_experience_graph(runtime).compile()
    state = ExperienceState(
        conversation_id=conversation.id,
        run_id=run.id,
        subject={"type": "experience", "id": str(created.experience_id)},
        scope={"field": "background"},
        run_kind="user_turn",
        tools_enabled=True,
        revision_snapshot={"scope": "field", "revision": revision},
        model_messages=[],
        tool_call=None,
        approval=None,
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
        and isinstance(part["data"]["data"]["tool_call_id"], int)
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
    service = AiChatService(
        registry,
        _ResultOnlyResumeRunner(),  # type: ignore[arg-type]
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
        assert await repos.tool_calls.save_validation(
            call,
            proposal_payload={"suggested_content": "不会被应用"},
            guard_payload={},
        )
        assert await repos.tool_calls.claim_approval_request(call.id)
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
        finished_run = await repos.runs.get(run.id)
        messages = await repos.messages.list_completed(conversation_id)
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
        approval=None,
    )
    handler = ContentChangeHandler()
    assert state["revision_snapshot"]["revision"] == 0
    assert handler.security is ToolSecurity.MEDIUM
    assert "suggested_content" in handler.description
    assert "content_change" not in system_prompt("zh", "background")


def test_experience_state_has_only_the_unified_tool_fields() -> None:
    """经历状态只保留统一工具调用和独立审批命令。"""
    state = ExperienceState(
        conversation_id=1,
        run_id=2,
        subject={"type": "experience", "id": "3"},
        scope={"field": "background"},
        run_kind="user_turn",
        tools_enabled=True,
        revision_snapshot={"scope": "field", "revision": 0},
        model_messages=[],
        raw_tool_call=None,
        tool_call=None,
        approval=None,
    )
    assert json.loads(json.dumps(state)) == state
    hints = get_type_hints(ExperienceState, include_extras=True)
    assert not {
        "proposal_id",
        "tool_call_id",
        "tool_phase",
        "tool_security",
        "tool_finished",
    }.intersection(hints)
    assert get_origin(hints["approval"]) is NotRequired


def test_dsml_compatibility_recovers_atomic_tool_call() -> None:
    """提供方泄漏的 DSML 被隔离解析，正文中不展示协议标签。"""
    fallback = DsmlToolCallFallback()
    visible = fallback.feed("说明<｜｜DSML｜｜tool_calls>")
    visible += fallback.feed(
        '<｜｜DSML｜｜invoke name="content_change">'
        '<｜｜DSML｜｜parameter name="scope" string="false">'
        '{"field":"background","evidence_id":null}'
        "</｜｜DSML｜｜parameter>"
        '<｜｜DSML｜｜parameter name="suggested_content" string="true">新背景'
        "</｜｜DSML｜｜parameter>"
        "</｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>结束"
    )
    calls, trailing = fallback.finish()

    assert visible + trailing == "说明结束"
    assert len(calls) == 1
    raw_call = json.loads(calls[0])
    assert raw_call["provider_id"] is None
    assert raw_call["name"] == "content_change"
    assert json.loads(raw_call["arguments"]) == {
        "scope": {"field": "background", "evidence_id": None},
        "suggested_content": "新背景",
    }


async def test_validator_reuses_run_index_when_provider_id_changes(
    isolated_db,
) -> None:
    """validator 按 run/index 复用调用，不信任重试生成的新 provider ID。"""
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

    revision = next(
        item.revision for item in created.field_states if item.key == "background"
    )
    state = ExperienceState(
        conversation_id=conversation.id,
        run_id=run.id,
        subject={"type": "experience", "id": str(created.experience_id)},
        scope={"field": "background"},
        run_kind="user_turn",
        tools_enabled=True,
        revision_snapshot={"scope": "field", "revision": revision},
        model_messages=[],
        tool_call=None,
        approval=None,
    )

    async def requested_id(provider_id: str) -> int:
        adapter = ExperienceAdapter()
        runtime = AiChatRuntime(
            _ConversationModel(provider_id),  # type: ignore[arg-type]
            ToolCallService(isolated_db.session, RepositoryFactory()),
            _PassthroughMemoryService(),  # type: ignore[arg-type]
        ).bind_tools(adapter.get_tool_handlers())
        graph = build_experience_graph(runtime).compile()
        parts = [
            part
            async for part in graph.astream(
                state,
                stream_mode=["custom", "updates"],
                version="v2",
            )
        ]
        requested = next(
            part
            for part in parts
            if part.get("type") == "custom"
            and part.get("data", {}).get("event") == "proposal.requested"
        )
        return int(requested["data"]["data"]["proposal_id"])

    first_id = await requested_id("first-provider-id")
    replay_id = await requested_id("retry-generated-id")
    assert replay_id == first_id
    async with isolated_db.session() as session:
        row = (
            await RepositoryFactory()
            .create(session)
            .tool_calls.get_by_run_index(run.id, 0)
        )
    assert row is not None
    assert row.id == first_id
    assert row.provider_tool_call_id == "first-provider-id"


def test_migration_moves_ordered_evidence_ids_and_drops_legacy_columns(
    tmp_path,
) -> None:
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
    with database._sync() as session:
        columns = (
            session.connection()
            .exec_driver_sql("PRAGMA table_info(experience_items)")
            .mappings()
            .all()
        )
        evidence_columns = (
            session.connection()
            .exec_driver_sql("PRAGMA table_info(evidence_items)")
            .mappings()
            .all()
        )
        state_columns = (
            session.connection()
            .exec_driver_sql("PRAGMA table_info(experience_field_states)")
            .mappings()
            .all()
        )
        migrations = set(
            session.connection()
            .exec_driver_sql("SELECT name FROM schema_migrations")
            .scalars()
            .all()
        )
        evidence_links = (
            session.connection()
            .exec_driver_sql(
                "SELECT experience_id, evidence_id, position "
                "FROM experience_evidence_items ORDER BY position"
            )
            .all()
        )
        revision_rows = set(
            session.connection()
            .exec_driver_sql(
                "SELECT scope, unit_key, ref_id, revision FROM experience_revisions"
            )
            .all()
        )
        evidence_field_keys = set(
            session.connection()
            .exec_driver_sql(
                "SELECT target_key FROM experience_field_states WHERE ref_id > 0"
            )
            .scalars()
            .all()
        )
    assert "raw_input" not in {column["name"] for column in columns}
    assert "evidence_ids" not in {column["name"] for column in columns}
    assert "revision" not in {column["name"] for column in state_columns}
    assert "background" in {column["name"] for column in evidence_columns}
    assert migrations == {
        "2026_08_01_experience_field_states",
        "2026_08_03_experience_evidence_items",
        "2026_08_04_experience_revisions",
        "2026_08_05_unified_experience_revision_units",
        "2026_08_07_ai_chat_tool_call_index",
        "2026_08_08_ai_chat_tool_call_state",
        "2026_08_08_ai_chat_conversation_scope",
        "2026_08_08_experience_chat_scope_field",
        "2026_08_10_ai_chat_memory_background",
        "2026_08_12_evidence_background",
    }
    assert evidence_field_keys == {"background", "action", "result"}
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
    """真实 LangGraph 与检查点器能暂停审批、应用字段并无工具续跑。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(
            ExperienceCreate(title="Agent", background="旧背景")
        )
    adapter = ExperienceAdapter()
    registry = AdapterRegistry()
    registry.register(adapter)
    repositories = RepositoryFactory()
    checkpoints = CheckpointLifecycle(tmp_path / "checkpoints.db")
    saver = await checkpoints.start()
    runtime = AiChatRuntime(
        _ConversationModel(),  # type: ignore[arg-type]
        ToolCallService(isolated_db.session, repositories),
        _PassthroughMemoryService(),  # type: ignore[arg-type]
    )
    runner = GraphRunner(registry, saver, runtime)
    service = AiChatService(registry, runner, repositories)
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
        proposal = next(
            event for event in events if event.event == "proposal.requested"
        )
        proposal_id = int(proposal.data["proposal_id"])
        async with isolated_db.session() as session:
            current_run = (
                await RepositoryFactory().create(session).runs.current(conversation_id)
            )
        assert current_run is not None
        assert current_run.status == "suspended"
        continued = [
            event
            async for event in service.resolve_proposal(
                proposal_id, "approve", "resolution-1"
            )
        ]
        assert any(event.event == "proposal.resolved" for event in continued)
        resolution = next(
            event for event in continued if event.event == "proposal.resolved"
        )
        assert resolution.data == {
            "proposal_id": proposal_id,
            "decision": "approve",
        }
        assert any(event.event == "content_change.applied" for event in continued)
        assert not any(event.event.startswith("assistant.") for event in continued)
        graph = runner._compiled(adapter)
        snapshot = await graph.aget_state(
            {"configurable": {"thread_id": f"ai-chat:{conversation_id}"}}
        )
        checkpoint_call = snapshot.values["tool_call"]
        assert "decision" not in checkpoint_call
        assert "client_resolution_id" not in checkpoint_call
        assert snapshot.values["approval"] == {
            "tool_call_id": proposal_id,
            "decision": "approve",
            "client_resolution_id": "resolution-1",
        }
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
        replayed_resolution = [
            event
            async for event in service.resolve_proposal(
                proposal_id,
                "approve",
                "resolution-1",
            )
        ]
        assert [event.event for event in replayed_resolution] == ["proposal.resolved"]
        assert replayed_resolution[0].data == {
            "proposal_id": proposal_id,
            "decision": "approve",
        }
        follow_up = [
            event
            async for event in service.stream_message(
                conversation_id, "继续", "message-2"
            )
        ]
        assert any(event.event == "assistant.completed" for event in follow_up)
        async with isolated_db.session() as session:
            delivered_call = (
                await RepositoryFactory().create(session).tool_calls.get(proposal_id)
            )
            assert delivered_call is not None
            assert delivered_call.delivery_status == "consumed"
    finally:
        await checkpoints.close()


async def test_real_graph_reject_never_executes_handler(isolated_db, tmp_path) -> None:
    """人工拒绝只形成 rejected 结果，不进入 Handler execute。"""
    async with isolated_db.session() as session:
        created = await ExperienceService(session).create(
            ExperienceCreate(title="拒绝验证", background="旧背景")
        )
    adapter = ExperienceAdapter()
    registry = AdapterRegistry()
    registry.register(adapter)
    repositories = RepositoryFactory()
    checkpoints = CheckpointLifecycle(tmp_path / "reject-checkpoints.db")
    saver = await checkpoints.start()
    runtime = AiChatRuntime(
        _ConversationModel(),  # type: ignore[arg-type]
        ToolCallService(isolated_db.session, repositories),
        _PassthroughMemoryService(),  # type: ignore[arg-type]
    )
    runner = GraphRunner(registry, saver, runtime)
    service = AiChatService(registry, runner, repositories)
    try:
        conversation_id = await service.create_conversation(
            "ExperienceAdapter",
            {"type": "experience", "id": str(created.experience_id)},
            {"field": "background"},
        )
        events = [
            event
            async for event in service.stream_message(
                conversation_id,
                "请改写背景",
                "reject-message",
            )
        ]
        proposal = next(
            event for event in events if event.event == "proposal.requested"
        )
        proposal_id = int(proposal.data["proposal_id"])
        resolved = [
            event
            async for event in service.resolve_proposal(
                proposal_id,
                "reject",
                "reject-resolution",
            )
        ]
        assert [event.event for event in resolved] == [
            "proposal.resolved",
            "content_change.rejected",
        ]
        assert resolved[0].data == {
            "proposal_id": proposal_id,
            "decision": "reject",
        }
        async with isolated_db.session() as session:
            detail = await ExperienceService(session).get(created.experience_id)
            row = await RepositoryFactory().create(session).tool_calls.get(proposal_id)
        assert detail.background == "旧背景"
        background = next(
            state for state in detail.field_states if state.key == "background"
        )
        assert background.revision == 0
        assert row is not None
        assert row.decision == "reject"
        assert row.tool_result == {"outcome": "rejected"}
    finally:
        await checkpoints.close()


async def test_failed_executor_second_identical_approval_heals(
    isolated_db,
    tmp_path,
) -> None:
    """A：失败后的第二个 approve/r1 直接重试 executor 并完成。"""
    handler = _FailingContentChangeHandler(failures=1)
    harness = await _start_graph_harness(
        isolated_db,
        tmp_path / "retry-identical.db",
        handler=handler,
    )
    try:
        proposal_id = await _request_proposal(harness, "retry-identical-message")
        first = [
            event
            async for event in harness.service.resolve_proposal(
                proposal_id,
                "approve",
                "r1",
            )
        ]
        assert [event.event for event in first] == ["run.failed"]
        assert first[0].data == {"code": "proposal_finalize_failed"}
        async with isolated_db.session() as session:
            row = await harness.repositories.create(session).tool_calls.get(proposal_id)
        assert row is not None
        assert (row.status, row.decision, row.client_resolution_id) == (
            "approved",
            "approve",
            "r1",
        )

        second = [
            event
            async for event in harness.service.resolve_proposal(
                proposal_id,
                "approve",
                "r1",
            )
        ]
        assert [event.event for event in second] == [
            "proposal.resolved",
            "content_change.applied",
        ]
        assert second[0].data == {
            "proposal_id": proposal_id,
            "decision": "approve",
        }
        assert handler.execute_count == 2
        async with isolated_db.session() as session:
            row = await harness.repositories.create(session).tool_calls.get(proposal_id)
            detail = await ExperienceService(session).get(harness.experience_id)
        assert row is not None
        assert row.status == "resolved"
        assert detail.background == "新背景"
    finally:
        await harness.checkpoints.close()


async def test_failed_executor_second_different_resolution_conflicts(
    isolated_db,
    tmp_path,
) -> None:
    """B：失败后的 reject/r2 在进入 Handler 前与 approve/r1 冲突。"""
    handler = _FailingContentChangeHandler(failures=1)
    harness = await _start_graph_harness(
        isolated_db,
        tmp_path / "retry-conflict.db",
        handler=handler,
    )
    try:
        proposal_id = await _request_proposal(harness, "retry-conflict-message")
        first = [
            event
            async for event in harness.service.resolve_proposal(
                proposal_id,
                "approve",
                "r1",
            )
        ]
        assert [event.event for event in first] == ["run.failed"]
        assert handler.execute_count == 1

        with pytest.raises(IdempotencyConflictError):
            _ = [
                event
                async for event in harness.service.resolve_proposal(
                    proposal_id,
                    "reject",
                    "r2",
                )
            ]
        assert handler.execute_count == 1
        async with isolated_db.session() as session:
            row = await harness.repositories.create(session).tool_calls.get(proposal_id)
            detail = await ExperienceService(session).get(harness.experience_id)
        assert row is not None
        assert (row.status, row.decision, row.client_resolution_id) == (
            "approved",
            "approve",
            "r1",
        )
        assert detail.background == "旧背景"
    finally:
        await harness.checkpoints.close()


async def test_repeated_executor_failures_each_yield_one_run_failed(
    isolated_db,
    tmp_path,
) -> None:
    """C：连续两次失败都收敛为单一 run.failed，不抛异常或空响应。"""
    handler = _FailingContentChangeHandler(failures=2)
    harness = await _start_graph_harness(
        isolated_db,
        tmp_path / "retry-twice.db",
        handler=handler,
    )
    try:
        proposal_id = await _request_proposal(harness, "retry-twice-message")
        for _ in range(2):
            events = [
                event
                async for event in harness.service.resolve_proposal(
                    proposal_id,
                    "approve",
                    "r1",
                )
            ]
            assert [(event.event, event.data) for event in events] == [
                ("run.failed", {"code": "proposal_finalize_failed"})
            ]
        assert handler.execute_count == 2
        async with isolated_db.session() as session:
            row = await harness.repositories.create(session).tool_calls.get(proposal_id)
        assert row is not None
        assert row.status == "approved"
    finally:
        await harness.checkpoints.close()


async def test_recovery_rejects_incomplete_checkpoint_approval(
    isolated_db,
    tmp_path,
) -> None:
    """自动推进前拒绝缺少任一审批身份字段的检查点。"""
    handler = _FailingContentChangeHandler(failures=0)
    harness = await _start_graph_harness(
        isolated_db,
        tmp_path / "incomplete-checkpoint-approval.db",
        handler=handler,
    )
    try:
        proposal_id = await _request_proposal(harness, "incomplete-approval-message")
        graph = harness.runner._compiled(harness.adapter)
        config = {"configurable": {"thread_id": f"ai-chat:{harness.conversation_id}"}}
        await graph.aupdate_state(
            config,
            {
                "approval": {
                    "tool_call_id": proposal_id,
                    "decision": "approve",
                },
            },
            as_node="executor",
        )
        with pytest.raises(IdempotencyConflictError):
            await harness.runner.ensure_interrupted(
                adapter_name=harness.adapter.adapter_name(),
                conversation_id=harness.conversation_id,
                approval={
                    "tool_call_id": proposal_id,
                    "decision": "approve",
                    "client_resolution_id": "r1",
                },
            )
        assert handler.execute_count == 0
    finally:
        await harness.checkpoints.close()


async def test_recovery_rejects_different_checkpoint_approval_before_executor(
    isolated_db,
    tmp_path,
) -> None:
    """检查点中的 approve/r1 不能被传入的 reject/r2 自动执行。"""
    handler = _FailingContentChangeHandler(failures=0)
    harness = await _start_graph_harness(
        isolated_db,
        tmp_path / "different-checkpoint-approval.db",
        handler=handler,
    )
    try:
        proposal_id = await _request_proposal(harness, "different-approval-message")
        graph = harness.runner._compiled(harness.adapter)
        config = {"configurable": {"thread_id": f"ai-chat:{harness.conversation_id}"}}
        await graph.aupdate_state(
            config,
            {
                "approval": {
                    "tool_call_id": proposal_id,
                    "decision": "approve",
                    "client_resolution_id": "r1",
                },
            },
            as_node="executor",
        )
        with pytest.raises(IdempotencyConflictError):
            await harness.runner.ensure_interrupted(
                adapter_name=harness.adapter.adapter_name(),
                conversation_id=harness.conversation_id,
                approval={
                    "tool_call_id": proposal_id,
                    "decision": "reject",
                    "client_resolution_id": "r2",
                },
            )
        assert handler.execute_count == 0
        async with isolated_db.session() as session:
            row = await harness.repositories.create(session).tool_calls.get(proposal_id)
        assert row is not None
        assert row.status == "awaiting_approval"
    finally:
        await harness.checkpoints.close()


@pytest.mark.parametrize("run_status", ["running", "failed"])
async def test_resolved_checkpoint_replays_undelivered_business_event(
    isolated_db,
    tmp_path,
    run_status,
) -> None:
    """工具已提交而运行未完成时，从持久化结果补发业务事件。"""
    handler = _FailingContentChangeHandler(failures=0)
    harness = await _start_graph_harness(
        isolated_db,
        tmp_path / "resolved-undelivered-event.db",
        handler=handler,
    )
    try:
        proposal_id = await _request_proposal(harness, "undelivered-event-message")
        approval = {
            "tool_call_id": proposal_id,
            "decision": "approve",
            "client_resolution_id": "r1",
        }
        async with isolated_db.session() as session:
            repositories = harness.repositories.create(session)
            row = await repositories.tool_calls.get(proposal_id)
            assert row is not None
            assert await repositories.runs.transition(
                row.run_id,
                from_statuses={"suspended"},
                to_status="running",
            )
            await session.commit()

        undelivered = [
            event
            async for event in harness.runner.resume(
                adapter_name=harness.adapter.adapter_name(),
                conversation_id=harness.conversation_id,
                approval=approval,
            )
        ]
        assert [event.event for event in undelivered] == [
            "proposal.resolved",
            "content_change.applied",
        ]
        assert undelivered[0].data == {
            "proposal_id": proposal_id,
            "decision": "approve",
        }
        assert handler.execute_count == 1
        async with isolated_db.session() as session:
            repositories = harness.repositories.create(session)
            row = await repositories.tool_calls.get(proposal_id)
            assert row is not None
            assert row.status == "resolved"
            if run_status == "failed":
                assert await repositories.runs.transition(
                    row.run_id,
                    from_statuses={"running"},
                    to_status="failed",
                )
            await session.commit()

        replayed = [
            event
            async for event in harness.service.resolve_proposal(
                proposal_id,
                "approve",
                "r1",
            )
        ]
        assert [event.event for event in replayed] == [
            "proposal.resolved",
            "content_change.applied",
        ]
        assert replayed[0].data == {
            "proposal_id": proposal_id,
            "decision": "approve",
        }
        assert replayed[1].data == undelivered[1].data
        assert replayed[1].data["tool_call_id"] == proposal_id
        assert replayed[1].data["outcome"] == "applied"
        assert handler.execute_count == 1
        async with isolated_db.session() as session:
            row = await harness.repositories.create(session).tool_calls.get(proposal_id)
            assert row is not None
            run = await harness.repositories.create(session).runs.get(row.run_id)
        assert run is not None
        assert run.status == "completed"
    finally:
        await harness.checkpoints.close()


async def test_suspended_checkpoint_identity_conflict_precedes_run_transition(
    isolated_db,
    tmp_path,
) -> None:
    """暂停态检查点的身份冲突应在运行转为执行中之前失败。"""
    handler = _FailingContentChangeHandler(failures=0)
    harness = await _start_graph_harness(
        isolated_db,
        tmp_path / "suspended-identity-conflict.db",
        handler=handler,
    )
    try:
        proposal_id = await _request_proposal(harness, "suspended-conflict-message")
        graph = harness.runner._compiled(harness.adapter)
        config = {"configurable": {"thread_id": f"ai-chat:{harness.conversation_id}"}}
        snapshot = await graph.aget_state(config)
        checkpoint_call = dict(snapshot.values["tool_call"])
        await graph.aupdate_state(
            config,
            {
                "tool_call": {
                    **checkpoint_call,
                    "tool_call_id": proposal_id + 100,
                }
            },
        )

        with pytest.raises(IdempotencyConflictError):
            _ = [
                event
                async for event in harness.service.resolve_proposal(
                    proposal_id,
                    "approve",
                    "r1",
                )
            ]
        assert handler.execute_count == 0
        async with isolated_db.session() as session:
            repositories = harness.repositories.create(session)
            row = await repositories.tool_calls.get(proposal_id)
            assert row is not None
            run = await repositories.runs.get(row.run_id)
        assert row.status == "awaiting_approval"
        assert run is not None
        assert run.status == "suspended"
    finally:
        await harness.checkpoints.close()


def test_tool_result_event_uses_persisted_tool_call_identity() -> None:
    """Tool Result 不能用业务 payload 覆盖持久化 Tool Call ID。"""
    event = tool_result_event(
        tool_name="content_change",
        tool_call_id=7,
        result={"outcome": "applied", "tool_call_id": 999},
    )
    assert event.event == "content_change.applied"
    assert event.data == {"outcome": "applied", "tool_call_id": 7}


async def test_cancelled_resolution_converges_claimed_run(
    isolated_db,
    tmp_path,
) -> None:
    """图已认领执行后取消时，运行不能永久停在执行中。"""
    handler = _BlockingContentChangeHandler()
    harness = await _start_graph_harness(
        isolated_db,
        tmp_path / "cancelled-resolution.db",
        handler=handler,
    )
    try:
        proposal_id = await _request_proposal(harness, "cancelled-resolution-message")

        async def resolve() -> list[AiChatEvent]:
            return [
                event
                async for event in harness.service.resolve_proposal(
                    proposal_id,
                    "approve",
                    "r1",
                )
            ]

        task = asyncio.create_task(resolve())
        await handler.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        async with isolated_db.session() as session:
            repositories = harness.repositories.create(session)
            row = await repositories.tool_calls.get(proposal_id)
            assert row is not None
            run = await repositories.runs.get(row.run_id)
        assert row.status == "approved"
        assert run is not None
        assert run.status == "cancelled"
    finally:
        await harness.checkpoints.close()


async def test_second_cancellation_cannot_interrupt_run_cleanup(
    isolated_db,
    tmp_path,
    monkeypatch,
) -> None:
    """清理事务被第二次取消时，仍须先让本次认领的运行收敛。"""
    handler = _BlockingContentChangeHandler()
    harness = await _start_graph_harness(
        isolated_db,
        tmp_path / "second-cancel-cleanup.db",
        handler=handler,
    )
    task: asyncio.Task[list[AiChatEvent]] | None = None
    release_cleanup = asyncio.Event()
    try:
        proposal_id = await _request_proposal(harness, "second-cancel-cleanup")
        original_transition = RunRepository.transition
        cleanup_entered = asyncio.Event()
        block_next_cleanup = True

        async def blocked_transition(
            repository: RunRepository,
            run_id: int,
            *,
            from_statuses,
            to_status: str,
            error_code: str | None = None,
        ) -> bool:
            nonlocal block_next_cleanup
            if (
                block_next_cleanup
                and from_statuses == {"running"}
                and to_status == "cancelled"
            ):
                block_next_cleanup = False
                cleanup_entered.set()
                await release_cleanup.wait()
            return await original_transition(
                repository,
                run_id,
                from_statuses=from_statuses,
                to_status=to_status,
                error_code=error_code,
            )

        async def resolve() -> list[AiChatEvent]:
            return [
                event
                async for event in harness.service.resolve_proposal(
                    proposal_id,
                    "approve",
                    "r1",
                )
            ]

        monkeypatch.setattr(RunRepository, "transition", blocked_transition)
        task = asyncio.create_task(resolve(), name="twice-cancelled-resolution")
        await handler.entered.wait()
        task.cancel()
        await cleanup_entered.wait()
        task.cancel()
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        async with isolated_db.session() as session:
            repositories = harness.repositories.create(session)
            row = await repositories.tool_calls.get(proposal_id)
            assert row is not None
            run = await repositories.runs.get(row.run_id)
        assert row.status == "approved"
        assert run is not None
        assert run.status == "cancelled"
    finally:
        handler.release.set()
        release_cleanup.set()
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await harness.checkpoints.close()


async def test_active_running_owner_blocks_second_resolution(
    isolated_db,
    tmp_path,
    monkeypatch,
) -> None:
    """B 不能把仍在 resume 前运行的 A 归一化后抢占。"""
    handler = _FailingContentChangeHandler(failures=0)
    harness = await _start_graph_harness(
        isolated_db,
        tmp_path / "active-running-owner.db",
        handler=handler,
    )
    task_a: asyncio.Task[list[AiChatEvent]] | None = None
    release_a_resume = asyncio.Event()
    try:
        proposal_id = await _request_proposal(harness, "active-running-owner")
        original_resume = harness.runner.resume
        a_before_resume = asyncio.Event()

        async def paused_resume(**kwargs):  # type: ignore[no-untyped-def]
            if asyncio.current_task() is task_a:
                a_before_resume.set()
                await release_a_resume.wait()
            async for event in original_resume(**kwargs):
                yield event

        async def resolve() -> list[AiChatEvent]:
            return [
                event
                async for event in harness.service.resolve_proposal(
                    proposal_id,
                    "approve",
                    "r1",
                )
            ]

        monkeypatch.setattr(harness.runner, "resume", paused_resume)
        task_a = asyncio.create_task(resolve(), name="active-resolution-owner-a")
        await a_before_resume.wait()

        blocked = False
        second_events: list[AiChatEvent] = []
        try:
            second_events = await resolve()
        except RunInProgressError:
            blocked = True

        async with isolated_db.session() as session:
            repositories = harness.repositories.create(session)
            row = await repositories.tool_calls.get(proposal_id)
            assert row is not None
            run = await repositories.runs.get(row.run_id)
        assert blocked, [event.event for event in second_events]
        assert handler.execute_count == 0
        assert row.status == "awaiting_approval"
        assert run is not None
        assert run.status == "running"
    finally:
        release_a_resume.set()
        if task_a is not None and not task_a.done():
            task_a.cancel()
        if task_a is not None:
            await asyncio.gather(task_a, return_exceptions=True)
        await harness.checkpoints.close()


async def test_cancelled_uncommitted_claim_cannot_cancel_next_owner(
    isolated_db,
    tmp_path,
    monkeypatch,
) -> None:
    """A 回滚后由 B 持久化的 running 不能被 A 的清理误取消。"""
    handler = _FailingContentChangeHandler(failures=0)
    harness = await _start_graph_harness(
        isolated_db,
        tmp_path / "cancelled-uncommitted-claim.db",
        handler=handler,
    )
    task_a: asyncio.Task[list[AiChatEvent]] | None = None
    task_b: asyncio.Task[list[AiChatEvent]] | None = None
    release_a_commit = asyncio.Event()
    release_b_resume = asyncio.Event()
    try:
        proposal_id = await _request_proposal(harness, "cancelled-claim-a")
        original_commit = AsyncSession.commit
        original_close = AsyncSession.close
        original_transition = RunRepository.transition
        original_resume = harness.runner.resume
        a_commit_entered = asyncio.Event()
        a_session_closed = asyncio.Event()
        b_before_resume = asyncio.Event()
        target_session: AsyncSession | None = None
        block_next_cancel_transition = True

        async def controlled_commit(session: AsyncSession) -> None:
            nonlocal target_session
            if target_session is None:
                target_session = session
                a_commit_entered.set()
                await release_a_commit.wait()
                await session.rollback()
                raise RuntimeError("simulated claim commit failure")
            await original_commit(session)

        async def tracked_close(session: AsyncSession) -> None:
            await original_close(session)
            if session is target_session:
                a_session_closed.set()

        async def controlled_transition(
            repository: RunRepository,
            run_id: int,
            *,
            from_statuses,
            to_status: str,
            error_code: str | None = None,
        ) -> bool:
            nonlocal block_next_cancel_transition
            if (
                block_next_cancel_transition
                and from_statuses == {"running"}
                and to_status == "cancelled"
            ):
                block_next_cancel_transition = False
                await b_before_resume.wait()
            return await original_transition(
                repository,
                run_id,
                from_statuses=from_statuses,
                to_status=to_status,
                error_code=error_code,
            )

        async def paused_resume(**kwargs):  # type: ignore[no-untyped-def]
            if asyncio.current_task() is task_b:
                b_before_resume.set()
                await release_b_resume.wait()
            async for event in original_resume(**kwargs):
                yield event

        async def resolve() -> list[AiChatEvent]:
            return [
                event
                async for event in harness.service.resolve_proposal(
                    proposal_id,
                    "approve",
                    "r1",
                )
            ]

        monkeypatch.setattr(AsyncSession, "commit", controlled_commit)
        monkeypatch.setattr(AsyncSession, "close", tracked_close)
        monkeypatch.setattr(RunRepository, "transition", controlled_transition)
        monkeypatch.setattr(harness.runner, "resume", paused_resume)

        task_a = asyncio.create_task(resolve(), name="cancelled-claim-owner-a")
        await a_commit_entered.wait()
        task_a.cancel()
        release_a_commit.set()
        await a_session_closed.wait()

        task_b = asyncio.create_task(resolve(), name="successful-claim-owner-b")
        await b_before_resume.wait()
        with pytest.raises(asyncio.CancelledError):
            await task_a

        assert handler.execute_count == 0
        async with isolated_db.session() as session:
            repositories = harness.repositories.create(session)
            row = await repositories.tool_calls.get(proposal_id)
            assert row is not None
            run = await repositories.runs.get(row.run_id)
        assert row.status == "awaiting_approval"
        assert run is not None
        assert run.status == "running"
    finally:
        release_a_commit.set()
        release_b_resume.set()
        for task in (task_a, task_b):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (task_a, task_b) if task is not None),
            return_exceptions=True,
        )
        await harness.checkpoints.close()


async def test_cancelled_committed_claim_converges_its_run(
    isolated_db,
    tmp_path,
    monkeypatch,
) -> None:
    """外层取消不能中断已开始的认领提交，提交后由 A 自己收敛。"""
    handler = _FailingContentChangeHandler(failures=0)
    harness = await _start_graph_harness(
        isolated_db,
        tmp_path / "cancelled-committed-claim.db",
        handler=handler,
    )
    task: asyncio.Task[list[AiChatEvent]] | None = None
    release_commit = asyncio.Event()
    try:
        proposal_id = await _request_proposal(harness, "cancelled-claim-success")
        original_commit = AsyncSession.commit
        commit_entered = asyncio.Event()
        block_next_commit = True

        async def blocked_commit(session: AsyncSession) -> None:
            nonlocal block_next_commit
            if block_next_commit:
                block_next_commit = False
                commit_entered.set()
                await release_commit.wait()
            await original_commit(session)

        async def resolve() -> list[AiChatEvent]:
            return [
                event
                async for event in harness.service.resolve_proposal(
                    proposal_id,
                    "approve",
                    "r1",
                )
            ]

        monkeypatch.setattr(AsyncSession, "commit", blocked_commit)
        task = asyncio.create_task(resolve(), name="cancelled-committed-claim")
        await commit_entered.wait()
        task.cancel()
        release_commit.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert handler.execute_count == 0
        async with isolated_db.session() as session:
            repositories = harness.repositories.create(session)
            row = await repositories.tool_calls.get(proposal_id)
            assert row is not None
            run = await repositories.runs.get(row.run_id)
        assert row.status == "awaiting_approval"
        assert run is not None
        assert run.status == "cancelled"
    finally:
        release_commit.set()
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await harness.checkpoints.close()


@pytest.mark.parametrize("cancel_outer", [False, True])
async def test_durable_claim_survives_session_close_failure(
    isolated_db,
    tmp_path,
    monkeypatch,
    cancel_outer,
) -> None:
    """提交已持久化后，会话关闭失败不能丢失认领所有权。"""
    handler = _FailingContentChangeHandler(failures=0)
    harness = await _start_graph_harness(
        isolated_db,
        tmp_path / "claim-close-failure.db",
        handler=handler,
    )
    task: asyncio.Task[list[AiChatEvent]] | None = None
    release_close = asyncio.Event()
    try:
        proposal_id = await _request_proposal(harness, "claim-close-failure")
        original_commit = AsyncSession.commit
        original_close = AsyncSession.close
        close_entered = asyncio.Event()
        claim_session: AsyncSession | None = None

        async def tracked_commit(session: AsyncSession) -> None:
            nonlocal claim_session
            if claim_session is None:
                claim_session = session
            await original_commit(session)

        async def close_then_fail(session: AsyncSession) -> None:
            await original_close(session)
            if session is claim_session:
                close_entered.set()
                await release_close.wait()
                raise RuntimeError("simulated claim session close failure")

        async def resolve() -> list[AiChatEvent]:
            return [
                event
                async for event in harness.service.resolve_proposal(
                    proposal_id,
                    "approve",
                    "r1",
                )
            ]

        monkeypatch.setattr(AsyncSession, "commit", tracked_commit)
        monkeypatch.setattr(AsyncSession, "close", close_then_fail)
        task = asyncio.create_task(resolve(), name="claim-close-failure")
        await close_entered.wait()
        if cancel_outer:
            task.cancel()
        release_close.set()

        events: list[AiChatEvent] = []
        if cancel_outer:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            events = await task

        async with isolated_db.session() as session:
            repositories = harness.repositories.create(session)
            row = await repositories.tool_calls.get(proposal_id)
            assert row is not None
            run = await repositories.runs.get(row.run_id)
        if cancel_outer:
            assert events == []
            assert run is not None
            assert run.status == "cancelled"
        else:
            assert [(event.event, event.data) for event in events] == [
                ("run.failed", {"code": "proposal_finalize_failed"})
            ]
            assert run is not None
            assert run.status == "failed"
        assert handler.execute_count == 0
        assert row.status == "awaiting_approval"
    finally:
        release_close.set()
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await harness.checkpoints.close()


async def test_postclaim_proposal_state_error_yields_one_run_failed(
    isolated_db,
    tmp_path,
) -> None:
    """图启动后的协议错误收敛为单一失败事件。"""
    handler = _ProposalStateFailingHandler()
    harness = await _start_graph_harness(
        isolated_db,
        tmp_path / "postclaim-proposal-state.db",
        handler=handler,
    )
    try:
        proposal_id = await _request_proposal(harness, "postclaim-state-message")
        events = [
            event
            async for event in harness.service.resolve_proposal(
                proposal_id,
                "approve",
                "r1",
            )
        ]
        assert [(event.event, event.data) for event in events] == [
            ("run.failed", {"code": "proposal_finalize_failed"})
        ]
        assert handler.execute_count == 1
        async with isolated_db.session() as session:
            repositories = harness.repositories.create(session)
            row = await repositories.tool_calls.get(proposal_id)
            assert row is not None
            run = await repositories.runs.get(row.run_id)
        assert row.status == "approved"
        assert run is not None
        assert run.status == "failed"
    finally:
        await harness.checkpoints.close()
