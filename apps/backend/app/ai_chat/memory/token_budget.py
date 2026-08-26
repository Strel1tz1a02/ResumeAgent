"""Conversation Memory 对通用模型预算能力的配置适配。"""

from app.ai_chat.memory.settings import memory_settings
from app.workflow_runtime.token_budget import (
    ModelTokenBudget,
    TokenEstimationError,
    build_structured_token_budget,
    count_request_tokens,
    count_text_tokens,
)
from app.workflow_runtime.token_budget import (
    build_model_token_budget as _build_model_token_budget,
)

MemoryTokenBudget = ModelTokenBudget


def build_memory_token_budget(
    *,
    requested_output: int | None = None,
    configured_input_cap: int | None = None,
) -> MemoryTokenBudget:
    return _build_model_token_budget(
        requested_output=requested_output or memory_settings.ai_chat_output_reserve,
        configured_input_cap=(
            configured_input_cap or memory_settings.ai_chat_input_cap
        ),
    )

__all__ = [
    "MemoryTokenBudget",
    "TokenEstimationError",
    "build_memory_token_budget",
    "build_structured_token_budget",
    "count_request_tokens",
    "count_text_tokens",
]
