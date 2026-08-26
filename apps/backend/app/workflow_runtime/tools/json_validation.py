"""工具调用边界共用的 JSON 值校验。"""

import math
from typing import Any

from app.workflow_runtime.errors import ToolProtocolError


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


def json_values_equal(left: Any, right: Any) -> bool:
    """按 JSON 类型和值比较，避免 Python 把 bool 与 int 视为相等。"""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        if left.keys() != right.keys():
            return False
        return all(
            json_values_equal(value, right[key])
            for key, value in left.items()
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            json_values_equal(left_value, right_value)
            for left_value, right_value in zip(left, right, strict=True)
        )
    return left == right
