"""LiteLLM streaming adapter used by business Graph nodes."""

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.ai_chat.tools.buffer import AssembledToolCall, ToolCallBuffer
from app.ai_chat.tools.handler import ToolHandler
from app.ai_chat.types import JsonObject
from app.llm import _calculate_timeout, get_router


def _get(value: Any, key: str, default: Any = None) -> Any:
    """Read a field from either a mapping or provider object."""
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _text(value: Any) -> str:
    """Normalize LiteLLM text content without surfacing reasoning fields."""
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
    """One visible assistant text fragment."""

    text: str


@dataclass(frozen=True)
class ToolCallsCompleted:
    """Complete Tool Calls emitted only after all fragments are assembled."""

    calls: tuple[AssembledToolCall, ...]


@dataclass(frozen=True)
class ModelCompleted:
    """Terminal model stream marker."""

    finish_reason: str | None


ModelStreamEvent = TextDelta | ToolCallsCompleted | ModelCompleted


class AiChatModel:
    """Stream visible text and atomically assembled Tool Calls through LiteLLM."""

    async def stream(
        self,
        *,
        messages: list[JsonObject],
        handlers: Mapping[str, ToolHandler],
        tools_enabled: bool,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ModelStreamEvent]:
        """Call the configured model and normalize its streaming response."""
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
                        "description": (handler.__doc__ or name).strip(),
                        "parameters": handler.schema(),
                    },
                }
                for name, handler in handlers.items()
            ]
        buffer = ToolCallBuffer()
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
                yield TextDelta(content)
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
        if calls:
            yield ToolCallsCompleted(tuple(calls))
        yield ModelCompleted(finish_reason)
