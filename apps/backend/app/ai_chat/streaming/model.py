"""供业务图节点使用的 LiteLLM 流式适配器。"""

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.ai_chat.tools.buffer import AssembledToolCall, ToolCallBuffer
from app.ai_chat.model_request import (
    ModelRequestChangedError,
    ModelRequestSpec,
    config_fingerprint,
    build_model_request_spec,
)
from app.ai_chat.tools.handler import ToolHandler
from app.ai_chat.streaming.compatibility import DsmlToolCallFallback
from app.ai_chat.types import JsonObject
from app.ai_chat.errors import ContextFullError
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


def _is_context_overflow(error: Exception) -> bool:
    name = type(error).__name__.lower()
    message = str(error).lower()
    return "contextwindow" in name or any(
        marker in message
        for marker in (
            "context length",
            "context window",
            "maximum context",
            "too many tokens",
            "max_input_tokens",
        )
    )


@dataclass(frozen=True)
class TextDelta:
    """一段可见的助手文本增量。"""

    text: str


@dataclass(frozen=True)
class ToolCallsCompleted:
    """仅在全部片段组装完成后发出的完整工具调用。"""

    calls: tuple[AssembledToolCall, ...]


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
        request_spec: ModelRequestSpec | None = None,
        handlers: Mapping[str, ToolHandler] | None = None,
        tools_enabled: bool | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        """调用已配置模型并规范化其流式响应。"""
        preflighted = request_spec is not None
        request_spec = request_spec or build_model_request_spec(
            handlers or {},
            tools_enabled=bool(tools_enabled),
            requested_output=max_tokens,
        )
        router, config = get_router()
        if preflighted and config_fingerprint(config) != request_spec.config_fingerprint:
            raise ModelRequestChangedError("model configuration changed after preflight")
        kwargs: dict[str, Any] = {
            "model": "primary",
            "messages": messages,
            "stream": True,
            "max_tokens": request_spec.max_tokens,
            "timeout": _calculate_timeout(
                "completion", request_spec.max_tokens, config.provider
            ),
        }
        if request_spec.reasoning_effort:
            kwargs["reasoning_effort"] = request_spec.reasoning_effort
        if request_spec.tools:
            kwargs["tools"] = request_spec.tools
        buffer = ToolCallBuffer()
        text_fallback = DsmlToolCallFallback() if request_spec.tools else None
        finish_reason: str | None = None
        async def provider_chunks():  # type: ignore[no-untyped-def]
            try:
                response = await router.acompletion(**kwargs)
                async for item in response:
                    yield item
            except Exception as exc:
                if _is_context_overflow(exc):
                    raise ContextFullError("provider_context_overflow") from exc
                raise

        async for chunk in provider_chunks():
            choices = _get(chunk, "choices", []) or [] # 候选回答
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
