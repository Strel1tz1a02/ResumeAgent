"""工具调用边界共用的 JSON 值校验。"""

import math
from typing import Any

from app.ai_chat.errors import ToolProtocolError


def ensure_finite_json(value: Any, *, message: str) -> None:
    """递归拒绝 JSON 中不能跨进程稳定表示的非有限浮点数。"""
    if isinstance(value, float) and not math.isfinite(value):
        raise ToolProtocolError(message)
    if isinstance(value, dict):
        for item in value.values():
            ensure_finite_json(item, message=message)
    elif isinstance(value, list):
        for item in value:
            ensure_finite_json(item, message=message)
