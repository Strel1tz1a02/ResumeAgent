"""经历图的可序列化状态。"""

from __future__ import annotations

from typing import NotRequired

from app.ai_chat.graph.state import (
    BaseState,
)
from app.ai_chat.tools.types import ApprovalDecision, ToolCall
from app.ai_chat.types import (
    JsonObject,
    JsonValue,  # noqa: F401 - 递归 JsonObject 类型的前向引用需要
)


class ExperienceState(BaseState):
    """经历适配器构造并由经历图持久化的完整状态。"""

    revision_snapshot: JsonObject
    model_messages: list[JsonObject]
    raw_tool_call: str | None
    tool_call: ToolCall | None

    approval: NotRequired[ApprovalDecision | None]
