"""经历 Graph 的可序列化 State。"""

from __future__ import annotations

from typing import Literal, NotRequired

from app.ai_chat.graph.state import (
    BaseState,
)
from app.ai_chat.types import (
    JsonObject,
    JsonValue,  # noqa: F401 - 递归 JsonObject 类型的前向引用需要
)


class ExperienceState(BaseState):
    """ExperienceAdapter 构造并由经历 Graph 持久化的完整状态。"""

    revision_snapshot: JsonObject
    model_messages: list[JsonObject]
    tool_call: JsonObject | None
    proposal_id: int | None
    tool_call_id: NotRequired[int | None]
    tool_phase: NotRequired[
        Literal["validated", "awaiting_approval", "approved", "resolved"] | None
    ]
    tool_security: NotRequired[Literal["low", "medium", "high"] | None]
    tool_finished: NotRequired[bool]
    approval: NotRequired[JsonObject | None]
