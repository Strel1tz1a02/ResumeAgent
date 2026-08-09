"""通用模型流对结构化和正文 Tool Call 的兼容测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.services.tool_call_service import ToolCallService
from app.ai_chat.streaming.model import (
    AiChatModel,
    ModelCompleted,
    TextDelta,
    ToolCallsCompleted,
)
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
    raw_call = json.loads(completed.calls[0])
    assert raw_call["name"] == "content_change"
    assert json.loads(raw_call["arguments"]) == {
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
