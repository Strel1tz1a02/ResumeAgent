"""通用模型流对结构化和正文 Tool Call 的兼容测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.ai_chat.context import ModelContext
from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.services.tool_service import ToolService
from app.ai_chat.streaming.model import AiChatModel, complete_tool_calls
from app.ai_chat.tools.operation import RegisteredTool
from app.ai_chat.tools.store import ToolCallStore
from app.experience import ExperienceAdapter
from app.experience.tools.content_change import (
    ContentChangeArguments,
    ContentChangeOperation,
)
from langchain_core.messages import AIMessageChunk


class _ChunkModel:
    def __init__(self, contents: list[str]) -> None:
        self._contents = contents

    def bind_tools(self, _tools):  # type: ignore[no-untyped-def]
        return self

    async def astream(self, _messages):  # type: ignore[no-untyped-def]
        for content in self._contents:
            yield AIMessageChunk(content=content)
        yield AIMessageChunk(content="", response_metadata={"finish_reason": "stop"})


class _MessageChunkModel:
    def __init__(self, chunks: list[AIMessageChunk]) -> None:
        self.chunks = chunks

    def bind_tools(self, _tools):  # type: ignore[no-untyped-def]
        return self

    async def astream(self, _messages):  # type: ignore[no-untyped-def]
        for chunk in self.chunks:
            yield chunk


class _RecordingModel:
    def __init__(self) -> None:
        self.tools = None
        self.messages = None
        self.max_tokens = None

    async def stream(  # type: ignore[no-untyped-def]
        self, *, tools, messages, **_kwargs
    ):
        self.tools = tools
        self.messages = messages
        self.max_tokens = _kwargs.get("max_tokens")
        yield AIMessageChunk(content="", response_metadata={"finish_reason": "stop"})


class _RecordingContextAssembler:
    def __init__(self) -> None:
        self.run_id = None
        self.context = None
        self.tools = None

    async def assemble(self, *, run_id, context, tools):  # type: ignore[no-untyped-def]
        self.run_id = run_id
        self.context = context
        self.tools = tools
        return list(context["messages"])


def _model_context(messages: list[dict]) -> ModelContext:  # type: ignore[type-arg]
    return {
        "instructions": "test instructions",
        "domain_sections": [],
        "messages": messages,
        "pending_tool_results": [],
    }


async def test_runtime_binding_is_an_immutable_snapshot(isolated_db) -> None:
    """绑定后的 Runtime 不受源字典后续修改，也不污染未绑定实例。"""
    tool = RegisteredTool(ContentChangeOperation())
    source = {tool.name: tool}
    base = AiChatRuntime(
        _RecordingModel(),  # type: ignore[arg-type]
        ToolService(ToolCallStore(isolated_db.session, RepositoryFactory())),
        _RecordingContextAssembler(),  # type: ignore[arg-type]
    )

    bound = base.bind_tools(source)
    source.clear()

    assert tuple(base.tools.model_tools) == ()
    assert tuple(bound.tools.model_tools) == ("content_change",)


async def test_runtime_exposes_only_registered_tools_to_model(isolated_db) -> None:
    """模型的 Tool Schema 输入只来自绑定后的 ToolService。"""
    model = _RecordingModel()
    tools = ToolService(ToolCallStore(isolated_db.session, RepositoryFactory()))
    runtime = AiChatRuntime(
        model,
        tools,
        _RecordingContextAssembler(),  # type: ignore[arg-type]
    ).bind_tools(ExperienceAdapter().get_tools())

    _ = [
        event
        async for event in runtime.stream_model(
            run_id=1,
            context=_model_context([]),
            tools_enabled=True,
        )
    ]

    assert model.tools == runtime.tools.model_tools
    assert model.max_tokens == 32_768


async def test_runtime_always_assembles_context_before_model(isolated_db) -> None:
    """业务只能提交 ModelContext，最终消息必须经过统一组装器。"""

    model = _RecordingModel()
    assembler = _RecordingContextAssembler()
    runtime = AiChatRuntime(
        model,  # type: ignore[arg-type]
        ToolService(ToolCallStore(isolated_db.session, RepositoryFactory())),
        assembler,  # type: ignore[arg-type]
    ).bind_tools(ExperienceAdapter().get_tools())
    messages = [
        {"role": "system", "content": "domain context"},
        {"role": "user", "content": "current question"},
    ]

    _ = [
        event
        async for event in runtime.stream_model(
            run_id=9,
            context=_model_context(messages),
            tools_enabled=True,
        )
    ]

    assert model.messages == messages
    assert assembler.run_id == 9
    assert assembler.context == _model_context(messages)
    assert assembler.tools is not None


async def test_recovers_deepseek_dsml_as_atomic_tool_call(monkeypatch) -> None:
    """DeepSeek 把 DSML 泄漏到正文时仍进入标准 Tool 生命周期。"""
    dsml = (
        '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="content_change">'
        '<｜｜DSML｜｜parameter name="scope" string="false">'
        '{"field":"technologies","evidence_id":null}'
        "</｜｜DSML｜｜parameter>"
        '<｜｜DSML｜｜parameter name="suggested_content" string="false">'
        '["Python","FastAPI"]'
        "</｜｜DSML｜｜parameter></｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>"
    )
    model = _ChunkModel([dsml[:19], dsml[19:77], dsml[77:]])
    config = SimpleNamespace(provider="deepseek", reasoning_effort=None)
    monkeypatch.setattr(
        "app.ai_chat.streaming.model.get_chat_model", lambda **_kwargs: (model, config)
    )

    events = [
        event
        async for event in AiChatModel().stream(
            messages=[{"role": "user", "content": "更新技能"}],
            tools={"content_change": RegisteredTool(ContentChangeOperation()).tool},
            tools_enabled=True,
        )
    ]

    assert not any("DSML" in str(chunk.content) for chunk in events)
    response = sum(events[1:], events[0])
    index, call = complete_tool_calls(response)[0]
    assert index == 0
    assert call["name"] == "content_change"
    assert call["args"] == {
        "scope": {"field": "technologies", "evidence_id": None},
        "suggested_content": ["Python", "FastAPI"],
    }
    assert all(isinstance(chunk, AIMessageChunk) for chunk in events)


async def test_assembles_langchain_native_tool_call_chunks(monkeypatch) -> None:
    """LangChain 标准工具片段必须原子组装为现有 Tool 生命周期输入。"""
    model = _MessageChunkModel(
        [
            AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "content_change",
                        "args": '{"scope":{"field":"technologies",',
                        "id": "call-1",
                        "index": 0,
                        "type": "tool_call_chunk",
                    }
                ],
            ),
            AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": None,
                        "args": '"evidence_id":null},"suggested_content":["Python"]}',
                        "id": None,
                        "index": 0,
                        "type": "tool_call_chunk",
                    }
                ],
                response_metadata={"finish_reason": "tool_calls"},
            ),
        ]
    )
    monkeypatch.setattr(
        "app.ai_chat.streaming.model.get_chat_model",
        lambda **_kwargs: (model, SimpleNamespace(provider="openai")),
    )

    events = [
        event
        async for event in AiChatModel().stream(
            messages=[{"role": "user", "content": "更新技能"}],
            tools={"content_change": RegisteredTool(ContentChangeOperation()).tool},
            tools_enabled=True,
        )
    ]

    response = sum(events[1:], events[0])
    index, call = complete_tool_calls(response)[0]
    assert index == 0
    assert call["id"] == "call-1"
    assert call["name"] == "content_change"
    assert call["args"]["suggested_content"] == ["Python"]
    assert response.response_metadata["finish_reason"] == "tool_calls"


def test_stream_boundary_rejects_incomplete_tool_call_arguments() -> None:
    """非法 JSON 分片必须停在流聚合边界，不能进入 ToolService。"""
    response = AIMessageChunk(
        content="",
        tool_call_chunks=[
            {
                "name": "content_change",
                "args": "{invalid",
                "id": "call-1",
                "index": 0,
                "type": "tool_call_chunk",
            }
        ],
    )

    with pytest.raises(ToolProtocolError, match="valid JSON"):
        complete_tool_calls(response)


def test_content_change_schema_uses_explicit_field_and_evidence_id() -> None:
    """模型看到明确的字段和 EvidenceItem 标识。"""
    arguments = ContentChangeArguments.model_validate(
        {
            "scope": {"field": "evidence", "evidence_id": 7},
            "suggested_content": {
                "background": None,
                "action": "优化召回链路",
                "result": "相关度提升",
            },
        }
    )
    assert arguments.scope.evidence_id == 7
    schema = RegisteredTool(ContentChangeOperation()).tool.tool_call_schema.model_json_schema()
    assert schema["properties"]["scope"]["$ref"]
    assert "anyOf" in schema["properties"]["suggested_content"]
