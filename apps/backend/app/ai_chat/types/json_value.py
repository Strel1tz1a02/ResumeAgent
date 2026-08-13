"""递归 JSON 值类型。"""

from typing import TypeAlias

from app.ai_chat.types.json_scalar import JsonScalar

JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
