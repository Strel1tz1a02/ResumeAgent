"""经历 Graph 的可序列化 State。"""

from __future__ import annotations

from pydantic import JsonValue as PydanticJsonValue

from app.ai_chat.types import (
    AdapterState,
    AiChatBaseState,
    ApprovalInput,  # noqa: F401 - LangGraph 在本模块解析继承的前向引用
    JsonObject,
    JsonValue,  # noqa: F401 - JsonObject 前向引用解析需要此导入
    PendingToolResult,  # noqa: F401 - 继承的 State 注解需要此导入
)


class ExperienceInputState(AdapterState):
    """ExperienceAdapter 为一次 Graph 调用构造的强类型业务输入。"""

    experience: dict[str, PydanticJsonValue]
    target_value: PydanticJsonValue
    normalized_target_value: PydanticJsonValue
    target_revision: int
    target_status: str
    evidence_revisions: dict[str, int]
    system_prompt: str
    model_messages: list[dict[str, PydanticJsonValue]]
    tools_enabled: bool


class ExperienceGraphState(AiChatBaseState, total=False):
    """通用字段之外，只保存可 JSON 序列化的经历上下文与 Tool 状态。"""

    experience: JsonObject
    target_value: JsonValue
    normalized_target_value: JsonValue
    target_revision: int
    target_status: str
    evidence_revisions: dict[str, int]
    system_prompt: str
    model_messages: list[JsonObject]
    response_text: str
    assembled_tool_call: JsonObject | None
    tool_dispatch: JsonObject | None
    tool_outcome: JsonObject | None
    resumed_approval: JsonObject | None
