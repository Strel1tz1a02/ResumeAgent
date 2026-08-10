"""会话记忆文档与摘要 Operations 的强校验。"""

from __future__ import annotations

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.ai_chat.memory.runs import (
    CORE_FIELDS,
    CORE_LIST_FIELDS,
    Memory,
    MemoryScalar,
)


class MemoryOperation(BaseModel):
    """摘要模型允许提出的单步变更。"""

    model_config = ConfigDict(extra="forbid")

    op: Literal["add", "update", "delete"]
    path: str
    value: MemoryScalar | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "MemoryOperation":
        """校验单条记忆操作的结构与类型。"""
        parts = self.path.split(".")
        if len(parts) != 2 or parts[0] not in {"core", "other"} or not parts[1]:
            raise ValueError("path must be core.<field> or other.<key>")
        namespace, field = parts
        if namespace == "core" and field not in CORE_FIELDS:
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
            if field in CORE_LIST_FIELDS and not isinstance(self.value, list):
                raise ValueError(f"{field} must be a string array")
        return self


def apply_operations(
    parent: Memory,
    operations: list[MemoryOperation],
    *,
    run_id: int | None = None,
) -> Memory:
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
    return Memory.model_validate(
        {
            **core,
            "other": other,
            "run_id": parent.run_id if run_id is None else run_id,
        }
    )
