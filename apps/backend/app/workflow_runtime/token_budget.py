"""模型请求的 Token 预算与可靠计数。"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import convert_to_messages
from pydantic import BaseModel, ConfigDict

from app.llm import (
    get_chat_model,
    get_llm_config,
    get_model_name,
    get_model_profile,
    get_safe_max_tokens,
)
from app.workflow_runtime.settings import workflow_runtime_settings
from app.workflow_runtime.types import JsonObject


class TokenEstimationError(RuntimeError):
    pass


class ModelTokenBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    max_tokens: int
    input_budget: int


def _conservative_token_count(value: object) -> int:
    payload = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    )
    return len(payload.encode("utf-8"))


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _model_limits() -> tuple[int | None, int | None]:
    info = get_model_profile()
    return (
        _positive_int(info.get("max_input_tokens")),
        _positive_int(info.get("max_output_tokens")),
    )


def build_model_token_budget(
    *,
    requested_output: int | None = None,
    configured_input_cap: int | None = None,
) -> ModelTokenBudget:
    config = get_llm_config()
    model = get_model_name(config)
    model_input, model_output = _model_limits()
    max_tokens = get_safe_max_tokens(
        model,
        requested_output or workflow_runtime_settings.model_output_reserve,
    )
    if model_output is not None:
        max_tokens = min(max_tokens, model_output)
    input_candidates = [
        configured_input_cap or workflow_runtime_settings.model_input_cap
    ]
    if model_input is not None:
        input_candidates.append(model_input)
    input_budget = (
        min(input_candidates) - workflow_runtime_settings.model_safety_margin
    )
    if input_budget <= 0:
        raise ValueError("Workflow input budget is not positive")
    return ModelTokenBudget(
        model=model,
        max_tokens=max_tokens,
        input_budget=input_budget,
    )


def build_structured_token_budget() -> ModelTokenBudget:
    model_input, _ = _model_limits()
    return build_model_token_budget(
        configured_input_cap=model_input or workflow_runtime_settings.model_input_cap
    )


def count_request_tokens(
    spec: ModelTokenBudget,
    messages: list[JsonObject],
    tools: list[JsonObject] | None = None,
) -> int:
    if get_model_name(get_llm_config()) != spec.model:
        raise TokenEstimationError("token budget model changed")
    try:
        config = get_llm_config()
        model, _ = get_chat_model(config, max_retries=0)
        count = model.get_num_tokens_from_messages(
            convert_to_messages(messages),
            tools=tools,
        )
    except Exception:  # noqa: BLE001
        count = _conservative_token_count({"messages": messages, "tools": tools or []})
    if not isinstance(count, int) or count < 0:
        raise TokenEstimationError("token counter returned an invalid value")
    return count


def count_text_tokens(spec: ModelTokenBudget, text: str) -> int:
    if get_model_name(get_llm_config()) != spec.model:
        raise TokenEstimationError("token budget model changed")
    try:
        config = get_llm_config()
        model, _ = get_chat_model(config, max_retries=0)
        count = model.get_num_tokens(text)
    except Exception:  # noqa: BLE001
        count = _conservative_token_count(text)
    if not isinstance(count, int) or count < 0:
        raise TokenEstimationError("token counter returned an invalid value")
    return count
