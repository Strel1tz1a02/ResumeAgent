"""通用模型流对结构化和正文 Tool Call 的兼容测试。"""

from __future__ import annotations

from types import SimpleNamespace

from app.ai_chat.streaming.model import (
    AiChatModel,
    ModelCompleted,
    TextDelta,
    ToolCallsCompleted,
)
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


async def test_recovers_deepseek_dsml_as_atomic_tool_call(monkeypatch) -> None:
    """DeepSeek 把 DSML 泄漏到正文时仍进入标准 Tool 生命周期。"""
    dsml = (
        '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="content_change">'
        '<｜｜DSML｜｜parameter name="target" string="false">'
        '{"key":"technologies","ref_id":null}'
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
        "target": {"key": "technologies", "ref_id": None},
        "suggested_content": ["Python", "FastAPI"],
    }
    assert isinstance(events[-1], ModelCompleted)


def test_content_change_schema_is_explicit_and_accepts_legacy_ref_id() -> None:
    """模型看到明确内容类型，同时兼容已生成的 ref_id 参数名。"""
    arguments = ContentChangeArguments.model_validate(
        {
            "target": {"key": "evidence", "ref_id": 7},
            "suggested_content": {
                "action": "优化召回链路",
                "result": "相关度提升",
                "metrics": None,
            },
        }
    )
    assert arguments.target.evidence_id == 7
    schema = ContentChangeHandler().schema()
    assert schema["properties"]["target"]["$ref"]
    assert "anyOf" in schema["properties"]["suggested_content"]
