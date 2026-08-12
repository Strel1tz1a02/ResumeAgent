"""经历字段目标、保存单元与完整状态的纯领域规则。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

EXPERIENCE_TARGET_KEYS = (
    "kind",
    "title",
    "organization",
    "role",
    "location",
    "start_date",
    "end_date",
    "is_current",
    "background",
    "technologies",
    "tags",
    "notes",
)
EVIDENCE_TARGET_KEYS = ("background", "action", "result")

_SAVE_UNITS: dict[str, tuple[str, ...]] = {
    "identity": ("kind", "title"),
    "dates": ("start_date", "end_date", "is_current"),
}


def save_unit_key(target_key: str) -> str:
    """返回字段唯一所属的经历保存单元键。"""
    for unit_key, fields in _SAVE_UNITS.items():
        if target_key in fields:
            return unit_key
    return target_key


def save_unit_fields(target_key: str) -> tuple[str, ...]:
    """返回目标字段所属、必须一起推进 revision 的保存单元。"""
    for fields in _SAVE_UNITS.values():
        if target_key in fields:
            return fields
    return (target_key,)


def normalize_field_value(target_key: str, value: Any) -> Any:
    """规范化字段值，用于 no-change 与审批 guard 比较。"""
    if isinstance(value, str):
        value = value.strip()
        return value or None if target_key not in {"title", "action"} else value
    if target_key in {"technologies", "tags"} and isinstance(value, list):
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                continue
            text = item.strip()
            marker = text.casefold()
            if text and marker not in seen:
                normalized.append(text)
                seen.add(marker)
        return normalized
    return value


def field_status(target_key: str, value: Any, values: Mapping[str, Any]) -> str:
    """根据已保存结构化值计算只供提醒使用的完善状态。"""
    if target_key == "kind":
        complete = bool(value and value != "other")
    elif target_key == "is_current":
        complete = True
    elif target_key == "end_date":
        complete = bool(values.get("is_current") or value)
    elif target_key in {"location", "tags", "notes"}:
        # 这些字段是可选增强项，空值不应制造永久警报。
        complete = True
    elif isinstance(value, list):
        complete = bool(value)
    elif isinstance(value, str):
        complete = bool(value.strip())
    else:
        complete = value is not None
    return "complete" if complete else "incomplete"
