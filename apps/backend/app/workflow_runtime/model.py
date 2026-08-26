"""Workflow 节点可显式注入的模型调用能力。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from langchain_core.messages import AIMessageChunk, ToolCall
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.llm import DEFAULT_JSON_MAX_TOKENS, get_chat_model
from app.workflow_runtime.compatibility import DsmlToolCallFallback
from app.workflow_runtime.context import ModelContext
from app.workflow_runtime.errors import ToolProtocolError
from app.workflow_runtime.tools import ModelToolSet
from app.workflow_runtime.types import JsonObject


class ModelContextAssembler(Protocol):
    async def assemble(
        self,
        *,
        run_id: int | str,
        context: ModelContext,
        tools: list[JsonObject] | None = None,
    ) -> list[JsonObject]: ...


class _ModelTransport(Protocol):
    async def stream(
        self,
        *,
        messages: list[JsonObject],
        tools: Mapping[str, BaseTool],
        tools_enabled: bool,
        max_tokens: int = DEFAULT_JSON_MAX_TOKENS,
    ) -> AsyncIterator[AIMessageChunk]: ...


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            else:
                text = _get(item, "text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


def complete_tool_calls(message: AIMessageChunk) -> list[tuple[int, ToolCall]]:
    if message.invalid_tool_calls:
        raise ToolProtocolError("Model produced an invalid streamed Tool Call")
    chunks = message.tool_call_chunks
    calls = message.tool_calls
    if len(chunks) != len(calls):
        raise ToolProtocolError("Streamed Tool Call could not be assembled completely")

    complete: list[tuple[int, ToolCall]] = []
    for position, (chunk, call) in enumerate(zip(chunks, calls, strict=True)):
        index = chunk.get("index")
        if index is None:
            index = position
        raw_arguments = chunk.get("args") or "{}"
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ToolProtocolError("Streamed Tool Call has an invalid index")
        if not isinstance(raw_arguments, str):
            raise ToolProtocolError("Streamed Tool Call arguments are not text")
        try:
            arguments = json.loads(
                raw_arguments,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, RecursionError, ValueError) as exc:
            raise ToolProtocolError(
                "Streamed Tool Call arguments are not valid JSON"
            ) from exc
        if not isinstance(arguments, dict) or arguments != call.get("args"):
            raise ToolProtocolError("Streamed Tool Call arguments are incomplete")
        complete.append((index, cast(ToolCall, call)))
    return complete


class _ConfiguredModelTransport:
    """获取当前模型并输出规范化的流式消息。"""

    async def stream(
        self,
        *,
        messages: list[JsonObject],
        tools: Mapping[str, BaseTool],
        tools_enabled: bool,
        max_tokens: int = DEFAULT_JSON_MAX_TOKENS,
    ) -> AsyncIterator[AIMessageChunk]:
        model, _ = get_chat_model(max_tokens=max_tokens)
        runnable: Any = model
        if tools_enabled and tools:
            runnable = model.bind_tools(list(tools.values()))
        text_fallback = DsmlToolCallFallback() if tools_enabled and tools else None
        async for chunk in runnable.astream(messages):
            content = _text(chunk.content)
            visible = text_fallback.feed(content) if text_fallback and content else content
            if visible == chunk.content:
                yield chunk
            else:
                yield chunk.model_copy(update={"content": visible})
        if text_fallback is not None:
            fallback_chunks, trailing_text = text_fallback.finish()
            if fallback_chunks or trailing_text:
                yield AIMessageChunk(
                    content=trailing_text,
                    tool_call_chunks=fallback_chunks,
                )


@dataclass(frozen=True)
class ModelClient:
    """Workflow 唯一公开的模型入口：组装上下文并流式调用模型。"""

    context: ModelContextAssembler
    model: _ModelTransport = field(default_factory=_ConfiguredModelTransport)
    max_tokens: int = DEFAULT_JSON_MAX_TOKENS

    async def stream_messages(
        self,
        *,
        messages: list[JsonObject],
        tools: Mapping[str, BaseTool],
        tools_enabled: bool,
        max_tokens: int | None = None,
    ) -> AsyncIterator[AIMessageChunk]:
        """调用已组装消息，供不需要 Conversation 上下文的一次性工作流使用。"""
        async for chunk in self.model.stream(
            messages=messages,
            tools=tools,
            tools_enabled=tools_enabled,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
        ):
            yield chunk

    async def stream(
        self,
        *,
        run_id: int | str,
        context: ModelContext,
        tools: ModelToolSet,
        tools_enabled: bool,
        max_tokens: int | None = None,
    ) -> AsyncIterator[AIMessageChunk]:
        model_tools = tools.model_tools
        tool_schemas = (
            [convert_to_openai_tool(tool) for tool in model_tools.values()]
            if tools_enabled
            else None
        )
        messages = await self.context.assemble(
            run_id=run_id,
            context=context,
            tools=tool_schemas,
        )
        async for chunk in self.stream_messages(
            messages=messages,
            tools=model_tools,
            tools_enabled=tools_enabled,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
        ):
            yield chunk
