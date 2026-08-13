"""JSON 对象类型。"""

from typing import TypeAlias

from app.ai_chat.types.json_value import JsonValue

JsonObject: TypeAlias = dict[str, JsonValue]
