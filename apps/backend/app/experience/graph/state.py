"""经历图的可序列化状态。"""

from __future__ import annotations

from typing import NotRequired, TypedDict

from langchain_core.messages import ToolCall as LangChainToolCall

from app.ai_chat.tools.types import ApprovalDecision, ToolCall
from app.ai_chat.types import (
    JsonObject,
    JsonValue,  # noqa: F401 - 递归 JsonObject 类型的前向引用需要
)


class ExperienceState(TypedDict):
    """经历适配器构造并由经历图持久化的完整状态。"""

    conversation_id: int
    run_id: int
    subject: JsonObject
    scope: JsonObject
    run_kind: str
    tools_enabled: bool
    revision_snapshot: JsonObject
    model_messages: list[JsonObject]
    raw_tool_call: LangChainToolCall | None
    raw_tool_call_index: NotRequired[int | None]
    tool_call: ToolCall | None

    approval: NotRequired[ApprovalDecision | None]
