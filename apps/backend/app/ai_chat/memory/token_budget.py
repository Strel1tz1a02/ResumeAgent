"""Memory 模块内部的最终请求 Token 预算。"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import convert_to_messages
from pydantic import BaseModel, ConfigDict

from app.ai_chat.memory.settings import memory_settings
from app.ai_chat.types import JsonObject
from app.llm import (
    get_chat_model,
    get_llm_config,
    get_model_name,
    get_model_profile,
    get_safe_max_tokens,
)


class TokenEstimationError(RuntimeError):
    """最终请求无法被可靠计数。"""


class MemoryTokenBudget(BaseModel):
    """Memory 选择历史时使用的模型与输入输出上限。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    max_tokens: int
    input_budget: int


def _conservative_token_count(value: object) -> int:
    """在 tokenizer 不可用时，以 UTF-8 字节数给出保守上界。"""
    if isinstance(value, str):
        payload = value
    else:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return len(payload.encode("utf-8"))


def _positive_int(value: Any) -> int | None:
    """把有效正整数规格标准化。"""
    return value if isinstance(value, int) and value > 0 else None


def _model_limits() -> tuple[int | None, int | None]:
    """从 LangChain 模型资料读取输入与输出 Token 上限。"""
    info = get_model_profile()
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
    model_input, model_output = _model_limits()
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
    tools: list[JsonObject] | None = None,
) -> int:
    """优先使用 LangChain tokenizer，并在离线环境采用保守上界。"""
    if get_model_name(get_llm_config()) != spec.model:
        raise TokenEstimationError("token budget model changed")
    try:
        config = get_llm_config()
        model, _ = get_chat_model(config, max_retries=0)
        count = model.get_num_tokens_from_messages(
            convert_to_messages(messages),
            tools=tools,
        )
    except Exception:
        count = _conservative_token_count({"messages": messages, "tools": tools or []})
    if not isinstance(count, int) or count < 0:
        raise TokenEstimationError("token counter returned an invalid value")
    return count


def count_text_tokens(spec: MemoryTokenBudget, text: str) -> int:
    """优先使用主模型 tokenizer，并在离线环境采用保守上界。"""
    if get_model_name(get_llm_config()) != spec.model:
        raise TokenEstimationError("token budget model changed")
    try:
        config = get_llm_config()
        model, _ = get_chat_model(config, max_retries=0)
        count = model.get_num_tokens(text)
    except Exception:
        count = _conservative_token_count(text)
    if not isinstance(count, int) or count < 0:
        raise TokenEstimationError("token counter returned an invalid value")
    return count
