"""最终模型请求的不可变规格与真实 Token 计数。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

import litellm
from pydantic import BaseModel, ConfigDict

from app.ai_chat.tools.handler import ToolHandler
from app.ai_chat.types import JsonObject
from app.config import settings
from app.llm import LLMConfig, get_llm_config, get_model_name, get_safe_max_tokens


class TokenEstimationError(RuntimeError):
    """最终请求无法被可靠计数。"""


class ModelRequestChangedError(RuntimeError):
    """预检后模型配置发生变化，旧规格不能继续使用。"""


class ModelRequestSpec(BaseModel):
    """预检与真实调用共同消费的非敏感请求规格。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    config_fingerprint: str
    tools: list[dict[str, Any]]
    max_tokens: int
    reasoning_effort: str | None
    input_budget: int


def config_fingerprint(config: LLMConfig) -> str:
    """散列影响请求语义的配置，不持久化任何密钥。"""
    key_digest = hashlib.sha256(config.api_key.encode("utf-8")).hexdigest()
    payload = {
        "provider": config.provider,
        "model": config.model,
        "api_base": config.api_base,
        "reasoning_effort": config.reasoning_effort,
        "api_key_digest": key_digest,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _model_limits(model: str) -> tuple[int | None, int | None]:
    try:
        info = litellm.get_model_info(model=model)
    except Exception:
        return None, None
    return (
        _positive_int(info.get("max_input_tokens")),
        _positive_int(info.get("max_output_tokens")),
    )


def build_model_request_spec(
    handlers: Mapping[str, ToolHandler],
    *,
    tools_enabled: bool,
    requested_output: int | None = None,
    configured_input_cap: int | None = None,
) -> ModelRequestSpec:
    """解析一次模型、Tools、输出上限和最终输入预算。"""
    config = get_llm_config()
    model = get_model_name(config)
    model_input, model_output = _model_limits(model)
    max_tokens = get_safe_max_tokens(
        model,
        requested_output or settings.ai_chat_output_reserve,
    )
    if model_output is not None:
        max_tokens = min(max_tokens, model_output)
    input_candidates = [configured_input_cap or settings.ai_chat_input_cap]
    if model_input is not None:
        input_candidates.append(model_input)
    input_budget = min(input_candidates) - settings.ai_chat_safety_margin
    if input_budget <= 0:
        raise ValueError("AI Chat input budget is not positive")
    tools: list[JsonObject] = []
    if tools_enabled:
        tools = [
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
    return ModelRequestSpec(
        model=model,
        config_fingerprint=config_fingerprint(config),
        tools=tools,
        max_tokens=max_tokens,
        reasoning_effort=config.reasoning_effort,
        input_budget=input_budget,
    )


def count_request_tokens(
    spec: ModelRequestSpec,
    messages: list[JsonObject],
) -> int:
    """按最终模型的 Messages + Tools 格式计数。"""
    try:
        count = litellm.token_counter(
            model=spec.model,
            messages=messages,
            tools=spec.tools or None,
        )
    except Exception as exc:
        raise TokenEstimationError(str(exc)) from exc
    if not isinstance(count, int) or count < 0:
        raise TokenEstimationError("token counter returned an invalid value")
    return count
