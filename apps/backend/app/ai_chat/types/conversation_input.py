"""ConversationService 交给业务 Workflow 的轮次输入。"""

from typing import TypedDict

from app.ai_chat.types.json_object import JsonObject


class ConversationInput(TypedDict):
    """启动一次对话 Workflow 所需的完整输入。"""

    conversation_id: int
    run_id: int
    subject: JsonObject
    scope: JsonObject
    language: str
    run_kind: str
    tools_enabled: bool
    messages: list[JsonObject]
    pending_tool_results: list[JsonObject]
