"""经历 Graph 的可序列化 State。"""

from __future__ import annotations

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
