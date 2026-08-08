"""供业务专用流式 API 消费的内部事件。"""

from dataclasses import dataclass

from app.ai_chat.types import JsonObject


@dataclass(frozen=True)
class AiChatEvent:
    """一个携带不透明 JSON 载荷的强类型后端事件。"""

    event: str
    data: JsonObject


def tool_result_event(
    *,
    tool_name: str,
    tool_call_id: int,
    result: JsonObject,
) -> AiChatEvent:
    """把持久化 Tool Result 映射为稳定业务事件。"""
    payload = dict(result)
    outcome = payload.get("outcome")
    event_name = (
        f"{tool_name}.{outcome}"
        if isinstance(outcome, str)
        else f"{tool_name}.completed"
    )
    return AiChatEvent(event_name, {**payload, "tool_call_id": tool_call_id})
