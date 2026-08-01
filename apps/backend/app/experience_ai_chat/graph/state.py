"""经历 Graph 的可序列化 State。"""

from __future__ import annotations

from typing import Any, NotRequired

from app.ai_chat.types import (
    AiChatBaseState,
    ApprovalInput,  # noqa: F401 - LangGraph resolves inherited ForwardRefs in this module
    JsonObject,
    JsonValue,  # noqa: F401 - required by JsonObject ForwardRef resolution
    PendingToolResult,  # noqa: F401 - required by inherited State annotations
)


class ExperienceGraphState(AiChatBaseState, total=False):
    """通用字段之外，只保存可 JSON 序列化的经历上下文与 Tool 状态。"""

    experience: JsonObject
    target_value: Any
    normalized_target_value: Any
    target_revision: int
    target_status: str
    system_prompt: str
    model_messages: list[JsonObject]
    response_text: str
    assembled_tool_call: JsonObject | None
    tool_dispatch: JsonObject | None
    tool_outcome: JsonObject | None
    resumed_approval: NotRequired[JsonObject]
