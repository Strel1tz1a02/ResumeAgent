"""供业务图节点使用的 LangChain 流式适配器。"""

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, cast

from langchain_core.messages import AIMessageChunk, ToolCall
from langchain_core.tools import BaseTool

from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.streaming.compatibility import DsmlToolCallFallback
from app.ai_chat.types import JsonObject
from app.llm import DEFAULT_JSON_MAX_TOKENS, get_chat_model


def _get(value: Any, key: str, default: Any = None) -> Any:
    """从映射或模型提供方对象中读取字段。"""
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _text(value: Any) -> str:
    """规范化 LangChain 文本内容，不暴露推理字段。"""
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
    """拒绝完整工具参数中的非标准 JSON 常量。"""
    raise ValueError(f"Unsupported JSON constant: {value}")


def complete_tool_calls(message: AIMessageChunk) -> list[tuple[int, ToolCall]]:
    """在流聚合边界把分片收束为带顺序的完整 LangChain ToolCall。"""
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


class AiChatModel:
    """通过 LangChain 流式输出可见文本和原子组装的工具调用。"""

    async def stream(
        self,
        *,
        messages: list[JsonObject],
        tools: Mapping[str, BaseTool],
        tools_enabled: bool,
        max_tokens: int = DEFAULT_JSON_MAX_TOKENS,
    ) -> AsyncIterator[AIMessageChunk]:
        """直接流式返回 LangChain 消息，并过滤正文中的兼容协议。"""
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
