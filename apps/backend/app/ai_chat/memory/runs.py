"""历史 Run 的原始内容、累计记忆及统一包装。"""

from __future__ import annotations

from dataclasses import dataclass
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.ai_chat.types import JsonObject

MemoryScalar = str | list[str]
EMPTY_CORE: dict[str, object] = {
    "current_goal": None,
    "constraints": [],
    "preferences": [],
    "confirmed_decisions": [],
    "open_questions": [],
}
CORE_FIELDS = frozenset(EMPTY_CORE)
CORE_LIST_FIELDS = CORE_FIELDS - {"current_goal"}
_OTHER_KEY = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


@dataclass(frozen=True)
class OriginRun:
    """一个终态 Run 的完整原始对话与工具调用。"""

    run_id: int
    kind: str
    status: str
    error_code: str | None
    messages: tuple[JsonObject, ...]
    tool_calls: tuple[JsonObject, ...]

    def history_record(self) -> JsonObject:
        """生成可写入历史 Prompt 的原始内容。"""
        return {
            "run_id": self.run_id,
            "kind": self.kind,
            "status": self.status,
            "error_code": self.error_code,
            "messages": [dict(message) for message in self.messages],
            "tool_calls": [dict(tool_call) for tool_call in self.tool_calls],
        }


class Memory(BaseModel):
    """截至指定 Run（包含该 Run）的累计记忆。"""

    model_config = ConfigDict(extra="forbid")

    run_id: int | None = None
    token_count: int = Field(default=0, ge=0)
    current_goal: str | None = None
    constraints: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    confirmed_decisions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    other: dict[str, MemoryScalar] = Field(default_factory=dict)

    @field_validator("current_goal")
    @classmethod
    def non_blank_goal(cls, value: str | None) -> str | None:
        """清理并校验当前目标。"""
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
        """清理并校验 Core 列表字段。"""
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("core arrays cannot contain blank items")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("core arrays cannot contain duplicates")
        return cleaned

    @field_validator("other")
    @classmethod
    def validate_other(
        cls,
        value: dict[str, MemoryScalar],
    ) -> dict[str, MemoryScalar]:
        """清理并校验动态记忆字段。"""
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
    def no_cross_core_duplicates(self) -> "Memory":
        """禁止相同内容重复出现在多个 Core 字段。"""
        values: list[str] = []
        if self.current_goal is not None:
            values.append(self.current_goal)
        for field in CORE_LIST_FIELDS:
            values.extend(getattr(self, field))
        if len(values) != len(set(values)):
            raise ValueError("the same memory cannot appear in multiple core fields")
        return self

    def core_json(self) -> dict[str, object]:
        """返回用于持久化的 Core 内容。"""
        return self.model_dump(
            exclude={"run_id", "token_count", "other"},
            mode="json",
        )

    def content_json(self) -> JsonObject:
        """返回用于历史 Prompt 的完整记忆内容。"""
        return {**self.core_json(), "other": self.other}


@dataclass(frozen=True)
class Run:
    """同一个历史 Run 的原始内容与累计记忆。"""

    origin: OriginRun
    memory: Memory | None = None

    def __post_init__(self) -> None:
        """确保原始内容与记忆属于同一个 Run。"""
        if self.memory is not None and self.memory.run_id != self.origin.run_id:
            raise ValueError("origin and memory must belong to the same run")

    @property
    def run_id(self) -> int:
        """返回统一包装对应的 Run ID。"""
        return self.origin.run_id

    def history_record(self) -> JsonObject:
        """返回该 Run 的完整原始内容。"""
        return self.origin.history_record()
