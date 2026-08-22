"""基于 LangChain 的多供应商模型适配层。"""

import json
import logging
import re
from functools import lru_cache
from typing import Any, Literal

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import convert_to_openai_messages
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from app.config import load_config_file, save_config_file, settings

LANGCHAIN_LOGGER_NAMES = (
    "langchain",
    "langchain_core",
    "langchain_openai",
    "langchain_anthropic",
    "langchain_google_genai",
)


def _configure_langchain_logging() -> None:
    """让 LangChain 相关日志级别与应用配置保持一致。"""
    numeric_level = getattr(logging, settings.log_llm, logging.WARNING)
    for logger_name in LANGCHAIN_LOGGER_NAMES:
        logging.getLogger(logger_name).setLevel(numeric_level)


_configure_langchain_logging()

# LLM timeout configuration (seconds) - base values
LLM_TIMEOUT_HEALTH_CHECK = 30
LLM_TIMEOUT_COMPLETION = 120
LLM_TIMEOUT_JSON = 180  # JSON completions may take longer

# JSON-010: JSON extraction safety limits
MAX_JSON_EXTRACTION_RECURSION = 10
MAX_JSON_CONTENT_SIZE = 1024 * 1024  # 1MB

# 所有未显式收紧预算的模型调用共用同一配置；保留旧名称兼容现有调用方。
DEFAULT_JSON_MAX_TOKENS = settings.llm_max_tokens

_USER_CHAT_PROMPT = ChatPromptTemplate.from_messages([("human", "{prompt}")])
_SYSTEM_USER_CHAT_PROMPT = ChatPromptTemplate.from_messages(
    [("system", "{system_prompt}"), ("human", "{prompt}")]
)


class LLMConfig(BaseModel):
    """LLM configuration model."""

    provider: str
    model: str
    api_key: str
    api_base: str | None = None
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None


def _build_messages(
    prompt: str,
    system_prompt: str | None = None,
) -> list[dict[str, Any]]:
    """使用 LangChain 模板构建统一消息。

    Args:
        prompt: 用户提示词。
        system_prompt: 可选的系统提示词；为空时不生成系统消息。

    Returns:
        LangChain 与业务图均可接受的 ``role``/``content`` 消息列表。
    """
    if system_prompt:
        messages = _SYSTEM_USER_CHAT_PROMPT.format_messages(
            system_prompt=system_prompt,
            prompt=prompt,
        )
    else:
        messages = _USER_CHAT_PROMPT.format_messages(prompt=prompt)
    return convert_to_openai_messages(messages)


def _normalize_api_base(provider: str, api_base: str | None) -> str | None:
    """按 LangChain 供应商集成的规则规范化 API 地址。

    LangChain 的专用集成直接接收供应商 base URL，因此保留用户填写的版本
    路径。Ollama 是例外：其客户端接收主机根地址，需移除常见 API 后缀。
    """
    # DeepSeek 使用标准 API 根地址，避免旧版 /beta 行为。
    if provider == "deepseek" and not api_base:
        return "https://api.deepseek.com"

    if not api_base:
        return None

    base = api_base.strip()
    if not base:
        return None

    base = base.rstrip("/")

    # Anthropic SDK 会自行追加 /v1/messages。
    if provider == "anthropic" and base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")

    # Google GenAI 客户端会自行追加版本路径。
    if provider == "gemini" and base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")

    # Ollama doesn't use /v1 paths. Strip common suffixes users might paste:
    # /v1, /api/chat, /api/generate
    if provider == "ollama":
        for suffix in ("/v1", "/api/chat", "/api/generate", "/api"):
            if base.endswith(suffix):
                base = base[: -len(suffix)].rstrip("/")
                break

    return base or None


# Sentinel passed to the OpenAI client when the user leaves api_key blank for
# openai_compatible. The client validates non-empty strings but not the value
# format; local servers that don't check auth ignore it.
_OPENAI_COMPATIBLE_SENTINEL = "sk-no-key"


def _effective_api_key(provider: str, api_key: str) -> str:
    """返回传给 LangChain 供应商集成的 API key。

    For openai_compatible with a blank key, substitute a sentinel so the
    OpenAI client accepts the call. Other providers pass through unchanged.
    """
    if provider == "openai_compatible" and not api_key:
        return _OPENAI_COMPATIBLE_SENTINEL
    return api_key


def _extract_text_parts(value: Any, depth: int = 0, max_depth: int = 10) -> list[str]:
    """Recursively extract text segments from nested response structures.

    Handles strings, lists, dicts with 'text'/'content'/'value' keys, and objects
    with text/content attributes. Limits recursion depth to avoid cycles.

    Args:
        value: Input value that may contain text in strings, lists, dicts, or objects.
        depth: Current recursion depth.
        max_depth: Maximum recursion depth before returning no content.

    Returns:
        A list of extracted text segments.
    """
    if depth >= max_depth:
        return []

    if value is None:
        return []

    if isinstance(value, str):
        return [value]

    if isinstance(value, list):
        parts: list[str] = []
        next_depth = depth + 1
        for item in value:
            parts.extend(_extract_text_parts(item, next_depth, max_depth))
        return parts

    if isinstance(value, dict):
        next_depth = depth + 1
        if "text" in value:
            return _extract_text_parts(value.get("text"), next_depth, max_depth)
        if "content" in value:
            return _extract_text_parts(value.get("content"), next_depth, max_depth)
        if "value" in value:
            return _extract_text_parts(value.get("value"), next_depth, max_depth)
        return []

    next_depth = depth + 1
    if hasattr(value, "text"):
        return _extract_text_parts(getattr(value, "text"), next_depth, max_depth)
    if hasattr(value, "content"):
        return _extract_text_parts(getattr(value, "content"), next_depth, max_depth)

    return []


def _join_text_parts(parts: list[str]) -> str | None:
    """Join text parts with newlines, filtering empty strings.

    Args:
        parts: Candidate text segments.

    Returns:
        Joined string or None if the result is empty.
    """
    joined = "\n".join(part for part in parts if part).strip()
    return joined or None


def _extract_message_text(message: Any) -> str | None:
    """从 LangChain 消息中提取跨供应商可见文本。

    Fallback order:
      1. message.content (standard OpenAI-compatible path)
      2. message.reasoning_content（DeepSeek/OpenAI 推理字段）
      3. message.thinking (Anthropic extended thinking)

    Reasoning-only responses are treated as valid content so thinking models
    can be used without special-casing them in every call site.
    """
    content: Any = None

    if hasattr(message, "content"):
        content = message.content
    elif isinstance(message, dict):
        content = message.get("content")

    text = _join_text_parts(_extract_text_parts(content))
    if text:
        return text

    # Fallback: reasoning_content (DeepSeek R1, OpenAI o1/o3).
    reasoning = _safe_get(message, "reasoning_content")
    text = _join_text_parts(_extract_text_parts(reasoning))
    if text:
        return text

    # Fallback: thinking (Anthropic extended thinking).
    thinking = _safe_get(message, "thinking")
    return _join_text_parts(_extract_text_parts(thinking))


def _safe_get(obj: Any, key: str) -> Any:
    """Get attribute or dict key from an object."""
    if hasattr(obj, key):
        return getattr(obj, key)
    if isinstance(obj, dict):
        return obj.get(key)
    additional = getattr(obj, "additional_kwargs", None)
    if isinstance(additional, dict) and key in additional:
        return additional.get(key)
    metadata = getattr(obj, "response_metadata", None)
    if isinstance(metadata, dict):
        return metadata.get(key)
    return None


def _to_code_block(content: str | None, language: str = "text") -> str:
    """Wrap content in a markdown code block for client display."""
    text = (content or "").strip()
    if not text:
        text = "<empty>"
    return f"```{language}\n{text}\n```"


# Regex for provider-style API-key tokens that may appear in upstream error
# messages (OpenAI / Anthropic / OpenRouter / DeepSeek all use ``sk-...``;
# Google AI Studio uses ``AIza...``). The OpenAI client already partially
# masks keys in its error text but leaves the first ~8 and last ~4 chars
# visible, which is enough to identify the provider and correlate with the
# user's stored key. We redact any remaining key-like run before we surface
# the message to the client via ``error_detail``.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # sk-<anything-non-whitespace>, covering both plain and already-masked
    # tokens (e.g., ``sk-ant-a****...7QAA``). Minimum length of 12 avoids
    # matching harmless substrings like ``sk-foo``.
    re.compile(r"sk-[A-Za-z0-9_\-*.]{12,}"),
    # Google AI Studio.
    re.compile(r"AIza[0-9A-Za-z_\-]{10,}"),
    # Generic Bearer tokens in an Authorization header line.
    re.compile(r"(?i)(Bearer\s+)[^\s\"']+"),
)


def _scrub_secrets(text: str) -> str:
    """Redact API-key-like substrings before the text leaves the server.

    Applied to ``error_detail`` on the failing-health-check path so that
    upstream exception messages (which may include partially-masked keys)
    can't be used by a Settings-page viewer to identify which provider /
    key variant is configured.
    """
    if not text:
        return text
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("<redacted>", redacted)
    return redacted


_PROVIDER_KEY_MAP: dict[str, str] = {
    "openai": "openai",
    "openai_compatible": "openai_compatible",
    "anthropic": "anthropic",
    "gemini": "google",
    "openrouter": "openrouter",
    "deepseek": "deepseek",
    "groq": "groq",
    "ollama": "ollama",
}


# Providers where the user commonly runs a local server without auth. For
# these, we MUST NOT fall back to ``settings.llm_api_key`` (the env-level
# default), because the env var may hold a real paid-API key that would then
# leak to a local/compatible endpoint the user set up expecting no auth.
_PROVIDERS_WITHOUT_ENV_KEY_FALLBACK: frozenset[str] = frozenset(
    {"openai_compatible", "ollama"}
)


def resolve_api_key(stored: dict, provider: str) -> str:
    """Resolve the effective API key from stored config.

    Priority: top-level ``api_key`` > ``api_keys[provider]`` > env/settings
    default — EXCEPT for providers in ``_PROVIDERS_WITHOUT_ENV_KEY_FALLBACK``
    (``openai_compatible`` / ``ollama``), where the env-level default is
    skipped so a paid OpenAI key in ``LLM_API_KEY`` cannot leak to a local
    self-hosted server when the user leaves the provider key blank.

    This is the single source of truth for key resolution. Every code path
    that needs an API key (runtime, config display, health check, test
    endpoint) must call this function instead of reading ``stored["api_key"]``
    directly.
    """
    api_key = stored.get("api_key", "")
    if not api_key:
        api_keys = stored.get("api_keys", {})
        if not isinstance(api_keys, dict):
            api_keys = {}
        config_provider = _PROVIDER_KEY_MAP.get(provider, provider)
        env_default = (
            ""
            if provider in _PROVIDERS_WITHOUT_ENV_KEY_FALLBACK
            else settings.llm_api_key
        )
        api_key = api_keys.get(config_provider, env_default)
    return api_key


def get_llm_config() -> LLMConfig:
    """Get current LLM configuration.

    Priority for api_key: top-level api_key > api_keys[provider] > env/settings
    Priority for reasoning_effort: config.json > env/settings

    Runs a one-shot migration for existing gpt-5 users: if provider is openai,
    model contains 'gpt-5', and reasoning_effort is ABSENT from config.json
    (not merely empty), persist reasoning_effort='minimal' to preserve the
    behavior the removed hardcoded branch provided. Users who clear the
    field explicitly (empty string persisted by the PUT handler) will not
    have it restored.
    """
    stored = load_config_file()
    provider = stored.get("provider", settings.llm_provider)
    model = stored.get("model", settings.llm_model)

    # One-shot migration: preserve old gpt-5 reasoning_effort behavior for
    # existing configs. Gated on ABSENT key so users can opt out by clearing
    # the field (PUT handler persists an empty string on clear).
    if (
        provider == "openai"
        and "gpt-5" in model.lower()
        and "reasoning_effort" not in stored
    ):
        stored["reasoning_effort"] = "minimal"
        try:
            save_config_file(stored)
            logging.info(
                "Migrated gpt-5 config to preserve reasoning_effort=minimal "
                "(set REASONING_EFFORT= or clear in Settings to disable)"
            )
        except Exception as e:
            # Non-fatal — retry on next call.
            logging.warning("Failed to persist gpt-5 migration: %s", e)

    api_key = resolve_api_key(stored, provider)

    raw_re = stored.get("reasoning_effort", settings.reasoning_effort)
    # Normalize empty string to None — user explicitly cleared.
    reasoning_effort = raw_re if raw_re else None

    return LLMConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        api_base=stored.get("api_base", settings.llm_api_base),
        reasoning_effort=reasoning_effort,
    )


def get_model_name(config: LLMConfig) -> str:
    """返回供应商实际接收的模型名，并兼容旧配置中的前缀。"""
    prefixes = {
        "openai": ("openai/",),
        "openai_compatible": ("openai/",),
        "anthropic": ("anthropic/",),
        "openrouter": ("openrouter/",),
        "gemini": ("gemini/", "google_genai/"),
        "deepseek": ("deepseek/",),
        "groq": ("groq/",),
        "ollama": ("ollama_chat/", "ollama/"),
    }
    model = config.model
    for prefix in prefixes.get(config.provider, ()):
        if model.startswith(prefix):
            return model[len(prefix) :]
    return model


_LANGCHAIN_PROVIDERS = {
    "openai": "openai",
    "openai_compatible": "openai",
    "anthropic": "anthropic",
    "openrouter": "openrouter",
    "gemini": "google_genai",
    "deepseek": "deepseek",
    "groq": "groq",
    "ollama": "ollama",
}


@lru_cache(maxsize=64)
def _cached_chat_model(
    provider: str,
    model_name: str,
    api_key: str,
    api_base: str | None,
    reasoning_effort: str | None,
    max_tokens: int | None,
    temperature: float | None,
    timeout: int,
    max_retries: int,
    disable_reasoning: bool,
) -> BaseChatModel:
    """创建并缓存参数完全相同的 LangChain ChatModel。"""
    model_provider = _LANGCHAIN_PROVIDERS.get(provider)
    if model_provider is None:
        raise ValueError(f"Unsupported LLM provider: {provider}")
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "max_retries": max_retries,
    }
    effective_key = _effective_api_key(provider, api_key)
    if provider != "ollama" and effective_key:
        kwargs["api_key"] = effective_key
    if api_base:
        kwargs["base_url"] = api_base
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if temperature is not None:
        kwargs["temperature"] = temperature
    if disable_reasoning and provider == "deepseek":
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    elif reasoning_effort:
        if provider in {"openai", "openai_compatible", "deepseek", "groq"}:
            kwargs["reasoning_effort"] = reasoning_effort
        elif provider == "gemini":
            kwargs["thinking_level"] = reasoning_effort
        elif provider == "anthropic" and reasoning_effort != "minimal":
            kwargs["effort"] = reasoning_effort
        elif provider == "openrouter":
            kwargs["reasoning"] = {"effort": reasoning_effort}
    return init_chat_model(
        model=model_name,
        model_provider=model_provider,
        **kwargs,
    )


def get_chat_model(
    config: LLMConfig | None = None,
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout: int | None = None,
    max_retries: int = 3,
    disable_reasoning: bool = False,
) -> tuple[BaseChatModel, LLMConfig]:
    """取得统一 LangChain ChatModel 及其应用配置。"""
    resolved = config or get_llm_config()
    model_name = get_model_name(resolved)
    actual_temperature = (temperature if _supports_temperature(model_name, temperature) else None)
    model = _cached_chat_model(
        resolved.provider,
        model_name,
        resolved.api_key,
        _normalize_api_base(resolved.provider, resolved.api_base),
        resolved.reasoning_effort,
        max_tokens,
        actual_temperature,
        timeout or _calculate_timeout("completion", max_tokens or 4096, resolved.provider),
        max_retries,
        disable_reasoning,
    )
    return model, resolved


async def check_llm_health(
    config: LLMConfig | None = None,
    *,
    include_details: bool = False,
    test_prompt: str | None = None,
) -> dict[str, Any]:
    """Check if the LLM provider is accessible and working."""
    if config is None:
        config = get_llm_config()

    # Check if API key is configured. Ollama and openai_compatible local
    # servers often run without auth, so a blank key is acceptable for those
    # providers — a sentinel is passed downstream (see _effective_api_key)
    # to satisfy the OpenAI client's non-empty-string validation.
    if config.provider not in ("ollama", "openai_compatible") and not config.api_key:
        return {
            "healthy": False,
            "provider": config.provider,
            "model": config.model,
            "error_code": "api_key_missing",
        }

    model_name = get_model_name(config)

    prompt = test_prompt or "Hi"

    try:
        model, _ = get_chat_model(
            config,
            max_tokens=64,
            timeout=LLM_TIMEOUT_HEALTH_CHECK,
            max_retries=0,
        )
        response = await model.ainvoke(_build_messages(prompt))
        content = _extract_message_text(response)
        response_model = (
            response.response_metadata.get("model_name")
            or response.response_metadata.get("model")
            or model_name
        )
        if not content:
            # LLM-003: Empty response (even after reasoning_content / thinking
            # 推理字段回退后仍为空时，健康检查必须失败。
            logging.warning(
                "LLM health check returned empty content",
                extra={"provider": config.provider, "model": config.model},
            )
            result: dict[str, Any] = {
                "healthy": False,
                "provider": config.provider,
                "model": config.model,
                "response_model": response_model,
                "error_code": "empty_content",
                "message": "LLM returned empty response",
            }
            if include_details:
                result["test_prompt"] = _to_code_block(prompt)
                result["model_output"] = _to_code_block(None)
            return result

        result = {
            "healthy": True,
            "provider": config.provider,
            "model": config.model,
            "response_model": response_model,
        }
        if include_details:
            result["test_prompt"] = _to_code_block(prompt)
            result["model_output"] = _to_code_block(content)
            # Surface reasoning/thinking text separately ONLY when the model
            # also returned distinct primary content. If message.content was
            # empty, _extract_message_text already folded the reasoning text
            # into `content` above — surfacing it here too would duplicate
            # identical text in "Model output" and "Model thinking".
            primary_content = _join_text_parts(
                _extract_text_parts(_safe_get(response, "content"))
            )
            reasoning_text = None
            if primary_content:
                reasoning_text = (
                    _join_text_parts(
                        _extract_text_parts(_safe_get(response, "reasoning_content"))
                    )
                    or _join_text_parts(
                        _extract_text_parts(_safe_get(response, "thinking"))
                    )
                )
            result["reasoning_content"] = (
                _to_code_block(reasoning_text) if reasoning_text else None
            )
        return result
    except Exception as e:
        # Log full exception details server-side, but do not expose them to clients
        logging.exception(
            "LLM health check failed",
            extra={"provider": config.provider, "model": config.model},
        )

        # Provide a minimal, actionable client-facing hint without leaking secrets.
        error_code = "health_check_failed"
        message = str(e)
        if "404" in message and "/v1/v1/" in message:
            error_code = "duplicate_v1_path"
        elif "404" in message:
            error_code = "not_found_404"
        elif "<!doctype html" in message.lower() or "<html" in message.lower():
            error_code = "html_response"
        result = {
            "healthy": False,
            "provider": config.provider,
            "model": config.model,
            "error_code": error_code,
        }
        if include_details:
            result["test_prompt"] = _to_code_block(prompt)
            result["model_output"] = _to_code_block(None)
            # Scrub api-key-like tokens before surfacing the upstream error
            # text so the Settings UI can't be used to read back even a
            # partially-masked copy of the configured key.
            result["error_detail"] = _to_code_block(_scrub_secrets(message))
        return result


async def complete(
    prompt: str,
    system_prompt: str | None = None,
    config: LLMConfig | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.7,
) -> str:
    """通过 LangChain ChatModel 完成一次文本生成。"""
    resolved = config or get_llm_config()
    effective_max_tokens = (
        max_tokens
        if max_tokens is not None
        else get_configured_max_tokens(resolved)
    )
    model, resolved = get_chat_model(
        resolved,
        max_tokens=effective_max_tokens,
        temperature=temperature,
        timeout=_calculate_timeout(
            "completion", effective_max_tokens, resolved.provider
        ),
    )
    model_name = get_model_name(resolved)

    messages = _build_messages(prompt, system_prompt)

    try:
        response = await model.ainvoke(messages)
        content = _extract_message_text(response)
        if not content:
            raise ValueError("Empty response from LLM")
        # Strip thinking tags from reasoning models (deepseek-r1, qwq, etc.)
        if "<think>" in content:
            content = _strip_thinking_tags(content)
            if not content:
                raise ValueError("Response contained only thinking content, no output")
        return content
    except Exception as e:
        # Log the actual error server-side for debugging
        logging.error(f"LLM completion failed: {e}", extra={
                      "model": model_name})
        raise ValueError(
            "LLM completion failed. Please check your API configuration and try again."
        ) from e


def _supports_json_mode(model_name: str) -> bool:
    """判断模型是否应尝试原生 JSON 模式。"""
    lowered = model_name.lower()
    return not lowered.startswith("claude-")


def _is_response_format_unsupported(error: Exception) -> bool:
    """Return True if a 400 indicates the server rejected ``response_format``.

    Some OpenAI-compatible servers (e.g. LM Studio, older llama.cpp builds)
    reject
    the ``{"type": "json_object"}`` we send for JSON mode, returning a 400 such
    as ``'response_format.type' must be 'json_schema' or 'text'`` (issue #857).

    Detecting this lets ``complete_json`` fall back to prompt-only JSON mode
    instead of failing the whole request, while genuine bad requests (e.g.
    context-length errors) still propagate.

    Requires both a mention of ``response_format`` *and* a rejection/validation
    cue, so that an unrelated 400 which merely names the parameter (e.g. a
    context-length error) does not trigger a pointless fallback retry. The cue
    list stays broad enough to catch varied provider wording ("must be ...",
    "not supported", "unsupported", "not allowed", "invalid") rather than any
    single provider's exact message.
    """
    msg = str(error).lower()
    if "response_format" not in msg:
        return False
    rejection_cues = ("must be", "not support", "unsupported", "not allowed", "invalid")
    return any(cue in msg for cue in rejection_cues)


def get_model_profile(config: LLMConfig | None = None) -> dict[str, Any]:
    """读取 LangChain 供应商集成附带的模型能力资料。"""
    try:
        model, _ = get_chat_model(config, max_retries=0)
        profile = getattr(model, "profile", None)
    except Exception:  # noqa: BLE001 - 能力资料缺失时按未知模型处理
        return {}
    if profile is None:
        return {}
    try:
        return dict(profile)
    except (TypeError, ValueError):
        return {}


def get_safe_max_tokens(
    model_name: str,
    requested: int = DEFAULT_JSON_MAX_TOKENS,
    *,
    config: LLMConfig | None = None,
) -> int:
    """按 LangChain 模型资料限制输出 token；未知模型保留调用方预算。

    Args:
        model_name: 供应商实际接收的模型名。
        requested: Desired token budget; defaults to DEFAULT_JSON_MAX_TOKENS.

    Returns:
        Safe token count, clamped correctly and always >= 1.
    """
    safe_requested = max(1, requested)

    resolved = config or get_llm_config()
    profile = (
        get_model_profile(resolved)
        if get_model_name(resolved) == model_name
        else {}
    )
    model_limit = profile.get("max_output_tokens")
    if isinstance(model_limit, int) and model_limit > 0:
        return min(safe_requested, model_limit)
    return safe_requested


def get_configured_max_tokens(config: LLMConfig | None = None) -> int:
    """返回统一配置、并按当前模型能力裁剪后的默认输出预算。"""
    resolved = config or get_llm_config()
    requested = settings.llm_max_tokens
    # LangChain 当前的 DeepSeek 注册资料仍可能报告过时的 8K 上限；官方
    # V4 Flash 路径已支持统一的 32K 默认预算，因此保持显式配置值。
    if resolved.provider == "deepseek":
        return requested
    return get_safe_max_tokens(
        get_model_name(resolved),
        requested,
        config=resolved,
    )


def _appears_truncated(data: dict, schema_type: str = "resume") -> bool:
    """LLM-001: Check if JSON data appears to be truncated.

    Detects suspicious patterns indicating incomplete responses.
    The checks are schema-aware so that enrichment/diff/keyword outputs
    are not evaluated against resume-structure heuristics.

    Args:
        data: Parsed JSON dict.
        schema_type: Expected schema — "resume" (full resume), "enrichment"
            (analyze output), "diff" (diff changes), "keywords", or
            "interview_prep".
            Determines which fields are checked for truncation.
    """
    if not isinstance(data, dict):
        return False

    if schema_type == "resume":
        # Full resume structure: check for empty required arrays
        suspicious_empty_arrays = ["workExperience", "education", "skills"]
        for key in suspicious_empty_arrays:
            if key in data and data[key] == []:
                # Log warning - these are rarely empty in real resumes
                logging.warning(
                    "Possible truncation detected: '%s' is empty",
                    key,
                )
                return True
        return False

    if schema_type == "enrichment":
        # Enrichment analyze returns items_to_enrich + questions.
        # Empty arrays are valid (resume is already strong).
        # Only flag if keys are entirely missing (LLM ignored structure).
        if "items_to_enrich" not in data or "questions" not in data:
            logging.warning(
                "Possible truncation detected: enrichment missing required keys"
            )
            return True
        return False

    if schema_type == "interview_prep":
        required = {
            "role_fit_analysis",
            "resume_questions",
            "project_follow_ups",
            "skill_gaps",
            "talking_points",
        }
        missing = required - set(data)
        if missing:
            logging.warning(
                "Possible truncation detected: interview_prep missing required keys: %s",
                ", ".join(sorted(missing)),
            )
            return True
        return False

    # For "diff", "keywords", and unknown schemas: no truncation heuristics.
    # Diff may legitimately return empty changes; keywords may return empty
    # lists when the job description has no actionable terms.
    return False


def _supports_temperature(model_name: str, temperature: float | None = None) -> bool:
    """根据已知供应商限制判断是否传递 temperature。

    Args:
        model_name: 供应商实际接收的模型名。
        temperature: The temperature value to check. If None, returns True
            (caller isn't setting a specific value).

    Returns:
        True if the model supports the given temperature, False otherwise.
    """
    if temperature is None:
        return True

    lowered = model_name.lower()
    if "claude-opus-4" in lowered:
        return False
    if "kimi-k2.6" in lowered and temperature != 1.0:
        return False
    if "gpt-5" in lowered or re.search(r"(?:^|[/_-])o[134](?:$|[/_.-])", lowered):
        return False
    return True


def _get_retry_temperature(model_name: str, attempt: int, base_temp: float = 0.1) -> float | None:
    """LLM-002: Get temperature for retry attempt.

    Returns None if the model does not support temperature at all.
    Returns 1.0 for models that only support temperature=1.
    Otherwise returns increasing temperatures for retry variation.
    """
    # Moonshot kimi-k2.6 only allows temperature=1.
    if "kimi-k2.6" in model_name.lower():
        return 1.0

    if not _supports_temperature(model_name, base_temp):
        return None

    temperatures = [base_temp, 0.3, 0.5, 0.7]
    return temperatures[min(attempt, len(temperatures) - 1)]


def _calculate_timeout(
    operation: str,
    max_tokens: int = 4096,
    provider: str = "openai",
) -> int:
    """LLM-005: Calculate adaptive timeout based on operation and parameters."""
    base_timeouts = {
        "health_check": LLM_TIMEOUT_HEALTH_CHECK,
        "completion": LLM_TIMEOUT_COMPLETION,
        "json": LLM_TIMEOUT_JSON,
    }

    base = base_timeouts.get(operation, LLM_TIMEOUT_COMPLETION)

    # Scale by token count (relative to 4096 baseline)
    token_factor = max(1.0, max_tokens / 4096)

    # Provider-specific latency adjustments
    provider_factors = {
        "openai": 1.0,
        "anthropic": 1.2,
        "openrouter": 1.5,  # More variable latency
        "groq": 1.0,
        "ollama": 2.0,  # Local models can be slower
    }
    provider_factor = provider_factors.get(provider, 1.0)

    return int(base * token_factor * provider_factor)


def _strip_thinking_tags(content: str) -> str:
    """Strip thinking/reasoning tags from model output.

    Ollama thinking models (deepseek-r1, qwq, etc.) wrap their reasoning
    in <think>...</think> tags. The actual answer follows after the closing
    tag. Strip these so JSON extraction finds the real output.
    """
    # Remove <think>...</think> blocks (including multiline)
    stripped = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    # Also handle unclosed <think> tag (model may still be "thinking" at end)
    stripped = re.sub(r"<think>.*", "", stripped, flags=re.DOTALL)
    return stripped.strip()


def _extract_json(content: str, _depth: int = 0) -> str:
    """Extract JSON from LLM response, handling various formats.

    LLM-001: Improved to detect and reject likely truncated JSON.
    LLM-007: Improved error messages for debugging.
    JSON-010: Added recursion depth and size limits.
    """
    # JSON-010: Safety limits
    if _depth > MAX_JSON_EXTRACTION_RECURSION:
        raise ValueError(
            f"JSON extraction exceeded max recursion depth: {_depth}")
    if len(content) > MAX_JSON_CONTENT_SIZE:
        raise ValueError(
            f"Content too large for JSON extraction: {len(content)} bytes")

    original = content

    # Strip thinking model tags (deepseek-r1, qwq, etc.)
    if "<think>" in content:
        content = _strip_thinking_tags(content)

    # Remove markdown code blocks
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        parts = content.split("```")
        if len(parts) >= 2:
            content = parts[1]
            # Remove language identifier if present (e.g., "json\n{...")
            if content.startswith(("json", "JSON")):
                content = content[4:]

    content = content.strip()

    # If content starts with {, find the matching }
    if content.startswith("{"):
        depth = 0
        end_idx = -1
        in_string = False
        escape_next = False

        for i, char in enumerate(content):
            if escape_next:
                escape_next = False
                continue
            if char == "\\":
                escape_next = True
                continue
            if char == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end_idx = i
                    break

        # LLM-001: Check for unbalanced braces - loop ended without depth reaching 0
        if end_idx == -1 and depth != 0:
            logging.warning(
                "JSON extraction found unbalanced braces (depth=%d), possible truncation",
                depth,
            )

        if end_idx != -1:
            return content[: end_idx + 1]

    # Try to find JSON object in the content (only if not already at start)
    start_idx = content.find("{")
    if start_idx > 0:
        # Only recurse if { is found after position 0 to avoid infinite recursion
        return _extract_json(content[start_idx:], _depth + 1)

    # LLM-007: Log unrecognized format for debugging
    logging.error(
        "Could not extract JSON from response format. Content preview: %s",
        content[:200] if content else "<empty>",
    )
    raise ValueError(f"No JSON found in response: {original[:200]}")


async def complete_json(
    prompt: str,
    system_prompt: str | None = None,
    config: LLMConfig | None = None,
    max_tokens: int | None = None,
    retries: int = 2,
    schema_type: str = "resume",
) -> dict[str, Any]:
    """通过 LangChain 生成并校验 JSON，保留内容质量重试。

    Args:
        schema_type: Expected schema — "resume", "enrichment", "diff",
            "keywords", or "interview_prep". Passed to _appears_truncated for
            context-aware truncation detection and used to tailor retry hints.
    """
    resolved = config or get_llm_config()
    model_name = get_model_name(resolved)
    effective_max_tokens = (
        max_tokens
        if max_tokens is not None
        else get_configured_max_tokens(resolved)
    )

    # Build messages
    json_system = (
        system_prompt or ""
    ) + "\n\nYou must respond with valid JSON only. No explanations, no markdown."
    messages = _build_messages(prompt, json_system)

    # Check if we can use JSON mode
    use_json_mode = _supports_json_mode(model_name)
    json_mode_failed = False

    for attempt in range(retries + 1):
        try:
            retry_temp = _get_retry_temperature(model_name, attempt)
            model, _ = get_chat_model(
                resolved,
                max_tokens=effective_max_tokens,
                temperature=retry_temp,
                timeout=_calculate_timeout(
                    "json", effective_max_tokens, resolved.provider
                ),
            )
            runnable: Any = model
            if use_json_mode and not json_mode_failed:
                if resolved.provider in {
                    "openai",
                    "openai_compatible",
                    "deepseek",
                    "groq",
                    "openrouter",
                }:
                    runnable = model.bind(response_format={"type": "json_object"})
                elif resolved.provider == "gemini":
                    runnable = model.bind(response_mime_type="application/json")
                elif resolved.provider == "ollama":
                    runnable = model.bind(format="json")

            response = await runnable.ainvoke(messages)
            content = _extract_message_text(response)

            if not content:
                raise ValueError("Empty response from LLM")

            logging.debug(
                f"LLM response (attempt {attempt + 1}): {content[:300]}")

            # Extract and parse JSON
            json_str = _extract_json(content)
            result = json.loads(json_str)

            # LLM-001: Check if parsed result appears truncated
            if isinstance(result, dict) and _appears_truncated(result, schema_type):
                if attempt < retries:
                    logging.warning(
                        "Parsed JSON appears truncated (attempt %d/%d), retrying",
                        attempt + 1,
                        retries + 1,
                    )
                    if schema_type == "resume":
                        hint = (
                            "\n\nIMPORTANT: Output the COMPLETE JSON object with ALL sections. Do not truncate."
                        )
                    elif schema_type == "enrichment":
                        hint = (
                            "\n\nIMPORTANT: Output the COMPLETE JSON object with ALL keys: items_to_enrich, questions, analysis_summary. Do not truncate."
                        )
                    elif schema_type == "interview_prep":
                        hint = (
                            "\n\nIMPORTANT: Output the COMPLETE JSON object with ALL keys: role_fit_analysis, resume_questions, project_follow_ups, skill_gaps, talking_points. Do not truncate."
                        )
                    else:
                        hint = (
                            "\n\nIMPORTANT: Output ONLY a valid JSON object. Start with { and end with }."
                        )
                    messages[-1]["content"] = prompt + hint
                    continue
                logging.warning(
                    "Parsed JSON appears truncated on final attempt, proceeding with result"
                )

            return result

        except json.JSONDecodeError as e:
            # Content quality — malformed JSON, retry with prompt hint
            logging.warning(f"JSON parse failed (attempt {attempt + 1}): {e}")
            if use_json_mode and not json_mode_failed:
                json_mode_failed = True
                logging.warning(
                    "JSON mode failed for %s, falling back to prompt-only (attempt %d)",
                    model_name, attempt + 1,
                )
            if attempt < retries:
                messages[-1]["content"] = (
                    prompt
                    + "\n\nIMPORTANT: Output ONLY a valid JSON object. Start with { and end with }."
                )
                continue
            raise ValueError(
                f"Failed to parse JSON after {retries + 1} attempts: {e}")

        except ValueError as e:
            # Content quality — empty response, JSON extraction failure
            logging.warning(f"Content extraction failed (attempt {attempt + 1}): {e}")
            if attempt < retries:
                continue
            raise

        except Exception as e:
            # 某些 OpenAI-compatible 服务拒绝 response_format；仅对此类错误
            # 回退到 prompt-only JSON，其他传输错误由供应商集成重试后继续抛出。
            if (
                use_json_mode
                and not json_mode_failed
                and _is_response_format_unsupported(e)
            ):
                json_mode_failed = True
                logging.warning(
                    "Provider rejected response_format for %s; falling back to "
                    "prompt-only JSON mode (attempt %d)",
                    model_name,
                    attempt + 1,
                )
                if attempt < retries:
                    continue
            raise

    raise ValueError(f"Failed after {retries + 1} attempts")
