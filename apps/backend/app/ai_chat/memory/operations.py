"""会话记忆文档与摘要 Operations 的强校验。"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MemoryScalar = str | list[str]

EMPTY_CORE: dict[str, object] = {
    "current_goal": None,
    "constraints": [],
    "preferences": [],
    "confirmed_decisions": [],
    "open_questions": [],
}
_CORE_FIELDS = frozenset(EMPTY_CORE)
_CORE_LIST_FIELDS = _CORE_FIELDS - {"current_goal"}
_OTHER_KEY = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


class MemoryDocument(BaseModel):
    """一份可注入上下文的累计式记忆。"""

    model_config = ConfigDict(extra="forbid")

    current_goal: str | None = None
    constraints: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    confirmed_decisions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    other: dict[str, MemoryScalar] = Field(default_factory=dict)

    @field_validator("current_goal")
    @classmethod
    def non_blank_goal(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("current_goal cannot be blank")
        return value

    @field_validator(
        "constraints", "preferences", "confirmed_decisions", "open_questions"
    )
    @classmethod
    def flat_non_blank_list(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("core arrays cannot contain blank items")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("core arrays cannot contain duplicates")
        return cleaned

    @field_validator("other")
    @classmethod
    def validate_other(cls, value: dict[str, MemoryScalar]) -> dict[str, MemoryScalar]:
        cleaned: dict[str, MemoryScalar] = {}
        for key, item in value.items():
            if _OTHER_KEY.fullmatch(key) is None:
                raise ValueError("other keys must be stable snake_case")
            if isinstance(item, str):
                normalized: MemoryScalar = item.strip()
                if not normalized:
                    raise ValueError("other values cannot be blank")
            else:
                normalized = [part.strip() for part in item]
                if any(not part for part in normalized):
                    raise ValueError("other arrays cannot contain blank items")
                if len(normalized) != len(set(normalized)):
                    raise ValueError("other arrays cannot contain duplicates")
            cleaned[key] = normalized
        return cleaned

    @model_validator(mode="after")
    def no_cross_core_duplicates(self) -> "MemoryDocument":
        values: list[str] = []
        if self.current_goal is not None:
            values.append(self.current_goal)
        for field in _CORE_LIST_FIELDS:
            values.extend(getattr(self, field))
        if len(values) != len(set(values)):
            raise ValueError("the same memory cannot appear in multiple core fields")
        return self

    def core_json(self) -> dict[str, object]:
        """返回持久化所需的固定 Core 结构。"""
        return self.model_dump(exclude={"other"}, mode="json")


class MemoryOperation(BaseModel):
    """摘要模型允许提出的单步变更。"""

    model_config = ConfigDict(extra="forbid")

    op: Literal["add", "update", "delete"]
    path: str
    value: MemoryScalar | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "MemoryOperation":
        parts = self.path.split(".")
        if len(parts) != 2 or parts[0] not in {"core", "other"} or not parts[1]:
            raise ValueError("path must be core.<field> or other.<key>")
        namespace, field = parts
        if namespace == "core" and field not in _CORE_FIELDS:
            raise ValueError("unknown core field")
        if self.op == "add" and namespace != "other":
            raise ValueError("add can only create other fields")
        if self.op == "delete":
            if self.value is not None:
                raise ValueError("delete cannot carry a value")
        elif self.value is None:
            raise ValueError("add and update require a value")
        if namespace == "core" and self.op == "update":
            if field == "current_goal" and not isinstance(self.value, str):
                raise ValueError("current_goal must be a string")
            if field in _CORE_LIST_FIELDS and not isinstance(self.value, list):
                raise ValueError(f"{field} must be a string array")
        return self


def apply_operations(
    parent: MemoryDocument,
    operations: list[MemoryOperation],
) -> MemoryDocument:
    """先校验整批操作，再原子物化新的累计 Memory。"""
    seen: set[str] = set()
    core = deepcopy(parent.core_json())
    other = deepcopy(parent.other)
    for operation in operations:
        if operation.path in seen:
            raise ValueError(f"conflicting operations for {operation.path}")
        seen.add(operation.path)
        namespace, field = operation.path.split(".", 1)
        if namespace == "other":
            exists = field in other
            if operation.op == "add" and exists:
                raise ValueError(f"other.{field} already exists")
            if operation.op in {"update", "delete"} and not exists:
                raise ValueError(f"other.{field} does not exist")
            if operation.op == "delete":
                del other[field]
            else:
                other[field] = deepcopy(operation.value)  # type: ignore[assignment]
            continue
        if operation.op == "delete":
            core[field] = None if field == "current_goal" else []
        else:
            core[field] = deepcopy(operation.value)
    return MemoryDocument.model_validate({**core, "other": other})
