"""通用图的输入、运行和审批恢复状态。"""

from typing import TypedDict

from app.ai_chat.types import JsonObject


class AdapterInput(TypedDict):
    """通用聊天层提供给适配器的统一调用输入。"""

    conversation_id: int
    run_id: int
    subject: JsonObject
    scope: JsonObject
    language: str
    run_kind: str
    tools_enabled: bool
    messages: list[JsonObject]
    pending_tool_results: list[JsonObject] # 工具结果回传使用


class BaseState(TypedDict):
    """所有业务图的公共状态基础。"""

    conversation_id: int
    run_id: int
    subject: JsonObject
    scope: JsonObject
    run_kind: str
    tools_enabled: bool
