"""通用模型流对结构化和正文 Tool Call 的兼容测试。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace

from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.services.tool_call_service import ToolCallService
from app.ai_chat.streaming.model import (
    AiChatModel,
    ModelCompleted,
    TextDelta,
    ToolCallsCompleted,
)
from app.ai_chat.tools.buffer import AssembledToolCall
from app.ai_chat.tools.handler import ToolContext
from app.ai_chat.tools.results import (
    ApprovalRequest,
    ApprovedToolCall,
    CompletedToolCall,
    PreparedToolCall,
)
from app.ai_chat.tools.security import ToolSecurity
from app.experience import ExperienceAdapter
from app.experience.tools.content_change import (
    ContentChangeArguments,
    ContentChangeHandler,
)


class _ChunkRouter:
    def __init__(self, contents: list[str]) -> None:
        self._contents = contents

    async def acompletion(self, **_kwargs):  # type: ignore[no-untyped-def]
        async def chunks():  # type: ignore[no-untyped-def]
            for content in self._contents:
                yield {
                    "choices": [
                        {"delta": {"content": content}, "finish_reason": None}
                    ]
                }
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

        return chunks()


class _RecordingModel:
    def __init__(self) -> None:
        self.handlers = None

    async def stream(self, *, handlers, **_kwargs):  # type: ignore[no-untyped-def]
        self.handlers = handlers
        yield ModelCompleted("stop")


@dataclass(frozen=True)
class _LegacyApprovalRequired:
    tool_call_id: int
    proposal_payload: dict


@dataclass(frozen=True)
class _LegacyToolCompleted:
    tool_call_id: int
    result: dict


class _RuntimeTools:
    def __init__(
        self,
        validation_states,
        *,
        approval_state=None,
        execution_result=None,
    ) -> None:  # type: ignore[no-untyped-def]
        self.validation_states = list(validation_states)
        self.approval_state = approval_state
        self.execution_result = execution_result
        self.request_count = 0
        self.execute_count = 0

    async def validate_call(self, _context, _call):  # type: ignore[no-untyped-def]
        return self.validation_states.pop(0)

    async def request_approval(self, _tool_call_id):  # type: ignore[no-untyped-def]
        self.request_count += 1
        return self.approval_state

    async def execute_call(self, _context, _tool_call_id):  # type: ignore[no-untyped-def]
        self.execute_count += 1
        return self.execution_result


def _install_legacy_dispatch_types(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    module = ModuleType("app.ai_chat.tools.lifecycle")
    module.ApprovalRequired = _LegacyApprovalRequired  # type: ignore[attr-defined]
    module.ToolCompleted = _LegacyToolCompleted  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.ai_chat.tools.lifecycle", module)


def _runtime_call() -> tuple[ToolContext, AssembledToolCall]:
    return (
        ToolContext(
            conversation_id=1,
            run_id=2,
            subject={"type": "experience", "id": "3"},
            scope={"field": "background"},
        ),
        AssembledToolCall(
            index=0,
            provider_id="provider-call",
            name="content_change",
            arguments={},
        ),
    )


async def test_runtime_binding_is_an_immutable_snapshot(isolated_db) -> None:
    """绑定后的 Runtime 不受源字典后续修改，也不污染未绑定实例。"""
    handler = ContentChangeHandler()
    source = {handler.name: handler}
    base = AiChatRuntime(
        _RecordingModel(),  # type: ignore[arg-type]
        ToolCallService(isolated_db.session, RepositoryFactory()),
    )

    bound = base.bind_tools(source)
    source.clear()

    assert tuple(base.tools.model_handlers) == ()
    assert tuple(bound.tools.model_handlers) == ("content_change",)


async def test_runtime_exposes_only_service_handlers_to_model(isolated_db) -> None:
    """模型的 Tool Schema 输入只来自绑定后的 ToolCallService。"""
    model = _RecordingModel()
    tools = ToolCallService(isolated_db.session, RepositoryFactory())
    runtime = AiChatRuntime(model, tools).bind_tools(
        ExperienceAdapter().get_tool_handlers()
    )

    _ = [
        event
        async for event in runtime.stream_model(messages=[], tools_enabled=True)
    ]

    assert model.handlers is runtime.tools.model_handlers


async def test_runtime_replays_awaiting_approval_without_side_effects(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """已等待审批的持久状态只映射旧 dispatch，不重复申请或执行。"""
    _install_legacy_dispatch_types(monkeypatch)
    tools = _RuntimeTools(
        [ApprovalRequest(7, "content_change", {"suggested_content": "新背景"})]
    )
    runtime = AiChatRuntime(_RecordingModel(), tools)  # type: ignore[arg-type]
    context, call = _runtime_call()

    result = await runtime.receive_tool_call(context=context, call=call)

    assert result == _LegacyApprovalRequired(7, {"suggested_content": "新背景"})
    assert tools.request_count == 0
    assert tools.execute_count == 0


async def test_runtime_executes_approved_once_then_replays_resolved(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """持久 approved 必须收敛为结果，后续 resolved 重放不能再次执行。"""
    _install_legacy_dispatch_types(monkeypatch)
    completed = CompletedToolCall(
        7,
        "content_change",
        {"outcome": "applied"},
        "approve",
        False,
    )
    tools = _RuntimeTools(
        [
            ApprovedToolCall(7, "content_change", "resolution-1"),
            CompletedToolCall(
                7,
                "content_change",
                {"outcome": "applied"},
                "approve",
                True,
            ),
        ],
        execution_result=completed,
    )
    runtime = AiChatRuntime(_RecordingModel(), tools)  # type: ignore[arg-type]
    context, call = _runtime_call()

    first = await runtime.receive_tool_call(context=context, call=call)
    replay = await runtime.receive_tool_call(context=context, call=call)

    assert first == _LegacyToolCompleted(7, {"outcome": "applied"})
    assert replay == first
    assert tools.request_count == 0
    assert tools.execute_count == 1


async def test_runtime_executes_approval_race_winner(monkeypatch) -> None:
    """request_approval 被并发推进到 approved 时仍继续执行并返回旧结果。"""
    _install_legacy_dispatch_types(monkeypatch)
    tools = _RuntimeTools(
        [PreparedToolCall(7, "content_change", ToolSecurity.MEDIUM)],
        approval_state=ApprovedToolCall(7, "content_change", "resolution-1"),
        execution_result=CompletedToolCall(
            7,
            "content_change",
            {"outcome": "applied"},
            "approve",
            False,
        ),
    )
    runtime = AiChatRuntime(_RecordingModel(), tools)  # type: ignore[arg-type]
    context, call = _runtime_call()

    result = await runtime.receive_tool_call(context=context, call=call)

    assert result == _LegacyToolCompleted(7, {"outcome": "applied"})
    assert tools.request_count == 1
    assert tools.execute_count == 1


async def test_recovers_deepseek_dsml_as_atomic_tool_call(monkeypatch) -> None:
    """DeepSeek 把 DSML 泄漏到正文时仍进入标准 Tool 生命周期。"""
    dsml = (
        '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="content_change">'
        '<｜｜DSML｜｜parameter name="scope" string="false">'
        '{"field":"technologies","evidence_id":null}'
        '</｜｜DSML｜｜parameter>'
        '<｜｜DSML｜｜parameter name="suggested_content" string="false">'
        '["Python","FastAPI"]'
        '</｜｜DSML｜｜parameter></｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>'
    )
    router = _ChunkRouter([dsml[:19], dsml[19:77], dsml[77:]])
    config = SimpleNamespace(provider="deepseek", reasoning_effort=None)
    monkeypatch.setattr("app.ai_chat.streaming.model.get_router", lambda: (router, config))

    events = [
        event
        async for event in AiChatModel().stream(
            messages=[{"role": "user", "content": "更新技能"}],
            handlers={"content_change": ContentChangeHandler()},
            tools_enabled=True,
        )
    ]

    assert not any(
        isinstance(event, TextDelta) and "DSML" in event.text for event in events
    )
    completed = next(event for event in events if isinstance(event, ToolCallsCompleted))
    assert completed.calls[0].name == "content_change"
    assert completed.calls[0].arguments == {
        "scope": {"field": "technologies", "evidence_id": None},
        "suggested_content": ["Python", "FastAPI"],
    }
    assert isinstance(events[-1], ModelCompleted)


def test_content_change_schema_uses_explicit_field_and_evidence_id() -> None:
    """模型看到明确的字段和 EvidenceItem 标识。"""
    arguments = ContentChangeArguments.model_validate(
        {
            "scope": {"field": "evidence", "evidence_id": 7},
            "suggested_content": {
                "action": "优化召回链路",
                "result": "相关度提升",
                "metrics": None,
            },
        }
    )
    assert arguments.scope.evidence_id == 7
    schema = ContentChangeHandler().schema()
    assert schema["properties"]["scope"]["$ref"]
    assert "anyOf" in schema["properties"]["suggested_content"]
