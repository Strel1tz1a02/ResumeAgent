"""供业务专用流式 API 消费的内部事件。"""
from dataclasses import dataclass

from app.ai_chat.types import JsonObject


@dataclass(frozen=True)
class AiChatEvent:
    """一个携带不透明 JSON 载荷的强类型后端事件。"""

    event: str
    data: JsonObject
