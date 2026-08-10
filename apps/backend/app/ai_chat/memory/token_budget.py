"""Memory 模块内部的最终请求 Token 预算。"""

from __future__ import annotations

from typing import Any

import litellm
from pydantic import BaseModel, ConfigDict

from app.ai_chat.memory.settings import memory_settings
from app.ai_chat.types import JsonObject
from app.llm import get_llm_config, get_model_name, get_safe_max_tokens


class TokenEstimationError(RuntimeError):
    """最终请求无法被可靠计数。"""


class MemoryTokenBudget(BaseModel):
    """Memory 选择历史时使用的模型与输入输出上限。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    max_tokens: int
    input_budget: int


def _positive_int(value: Any) -> int | None:
    """把有效正整数规格标准化。"""
    return value if isinstance(value, int) and value > 0 else None


def _model_limits(model: str) -> tuple[int | None, int | None]:
    """读取模型的输入与输出 Token 上限。"""
    try:
        info = litellm.get_model_info(model=model)
    except Exception:
        return None, None
    return (
        _positive_int(info.get("max_input_tokens")),
        _positive_int(info.get("max_output_tokens")),
    )


def build_memory_token_budget(
    *,
    requested_output: int | None = None,
    configured_input_cap: int | None = None,
) -> MemoryTokenBudget:
    """解析历史选择所需的模型、输出预留和输入预算。"""
    config = get_llm_config()
    model = get_model_name(config)
    model_input, model_output = _model_limits(model)
    max_tokens = get_safe_max_tokens(
        model,
        requested_output or memory_settings.ai_chat_output_reserve,
    )
    if model_output is not None:
        max_tokens = min(max_tokens, model_output)
    input_candidates = [configured_input_cap or memory_settings.ai_chat_input_cap]
    if model_input is not None:
        input_candidates.append(model_input)
    input_budget = min(input_candidates) - memory_settings.ai_chat_safety_margin
    if input_budget <= 0:
        raise ValueError("AI Chat input budget is not positive")
    return MemoryTokenBudget(
        model=model,
        max_tokens=max_tokens,
        input_budget=input_budget,
    )


def count_request_tokens(
    spec: MemoryTokenBudget,
    messages: list[JsonObject],
) -> int:
    """按摘要模型的 Messages 格式计数。"""
    try:
        count = litellm.token_counter(
            model=spec.model,
            messages=messages,
        )
    except Exception as exc:
        raise TokenEstimationError(str(exc)) from exc
    if not isinstance(count, int) or count < 0:
        raise TokenEstimationError("token counter returned an invalid value")
    return count


def count_text_tokens(spec: MemoryTokenBudget, text: str) -> int:
    """使用主模型 Tokenizer 计算一个待组装 Prompt 字符串。"""
    try:
        count = litellm.token_counter(model=spec.model, text=text)
    except Exception as exc:
        raise TokenEstimationError(str(exc)) from exc
    if not isinstance(count, int) or count < 0:
        raise TokenEstimationError("token counter returned an invalid value")
    return count
