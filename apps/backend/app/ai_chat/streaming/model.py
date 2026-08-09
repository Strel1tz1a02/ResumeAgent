"""供业务图节点使用的 LiteLLM 流式适配器。"""

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.ai_chat.tools.buffer import ToolCallBuffer
from app.ai_chat.tools.handler import ToolHandler
from app.ai_chat.streaming.compatibility import DsmlToolCallFallback
from app.ai_chat.types import JsonObject
from app.llm import _calculate_timeout, get_router


def _get(value: Any, key: str, default: Any = None) -> Any:
    """从映射或模型提供方对象中读取字段。"""
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _text(value: Any) -> str:
    """规范化 LiteLLM 文本内容，不暴露推理字段。"""
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


@dataclass(frozen=True)
class TextDelta:
    """一段可见的助手文本增量。"""

    text: str


@dataclass(frozen=True)
class ToolCallsCompleted:
    """仅在全部片段组装完成后发出的原始工具调用字符串。"""

    calls: tuple[str, ...]


@dataclass(frozen=True)
class ModelCompleted:
    """模型流结束标记。"""

    finish_reason: str | None


ModelStreamEvent = TextDelta | ToolCallsCompleted | ModelCompleted


class AiChatModel:
    """通过 LiteLLM 流式输出可见文本和原子组装的工具调用。"""

    async def stream(
        self,
        *,
        messages: list[JsonObject],
        handlers: Mapping[str, ToolHandler],
        tools_enabled: bool,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ModelStreamEvent]:
        """调用已配置模型并规范化其流式响应。"""
        router, config = get_router()
        kwargs: dict[str, Any] = {
            "model": "primary",
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens,
            "timeout": _calculate_timeout("completion", max_tokens, config.provider),
        }
        if config.reasoning_effort:
            kwargs["reasoning_effort"] = config.reasoning_effort
        if tools_enabled and handlers:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": handler.description.strip(),
                        "parameters": handler.schema(),
                    },
                }
                for name, handler in handlers.items()
            ]
        buffer = ToolCallBuffer()
        text_fallback = DsmlToolCallFallback() if tools_enabled and handlers else None
        finish_reason: str | None = None
        response = await router.acompletion(**kwargs)
        async for chunk in response:
            choices = _get(chunk, "choices", []) or []
            if not choices:
                continue
            choice = choices[0]
            delta = _get(choice, "delta", {})
            content = _text(_get(delta, "content"))
            if content:
                visible = text_fallback.feed(content) if text_fallback else content
                if visible:
                    yield TextDelta(visible)
            for call in _get(delta, "tool_calls", []) or []:
                function = _get(call, "function", {})
                buffer.add(
                    index=int(_get(call, "index", 0) or 0),
                    provider_id=_get(call, "id"),
                    name=_get(function, "name"),
                    arguments=_get(function, "arguments"),
                )
            reason = _get(choice, "finish_reason")
            if reason:
                finish_reason = str(reason)
        calls = buffer.assemble()
        if text_fallback is not None:
            fallback_calls, trailing_text = text_fallback.finish()
            if trailing_text:
                yield TextDelta(trailing_text)
            if not calls:
                calls = fallback_calls
        if calls:
            yield ToolCallsCompleted(tuple(calls))
        yield ModelCompleted(finish_reason)
