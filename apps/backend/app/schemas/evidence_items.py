"""结构化经历证据的 Pydantic 契约。"""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EvidenceCreate(BaseModel):
    """客户端创建证据记录时提供的字段。"""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1)
    result: str | None = None
    metrics: str | None = None

    @field_validator("action")
    @classmethod
    def _action_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("action must not be blank")
        return value


class EvidenceUpdate(BaseModel):
    """客户端对一条证据记录提供的局部更新。"""

    model_config = ConfigDict(extra="forbid")

    action: str | None = None
    result: str | None = None
    metrics: str | None = None
    expected_revision: int | None = Field(default=None, ge=0)

    @field_validator("action")
    @classmethod
    def _provided_action_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("action must not be blank")
        return value


class EvidenceRead(BaseModel):
    """返回给客户端的已持久化证据记录。"""

    id: int
    action: str
    result: str | None = None
    metrics: str | None = None
    created_at: str
    updated_at: str


class EvidenceReorder(BaseModel):
    """请求的展示顺序；服务层会校验其与当前 ID 集合一致。"""

    model_config = ConfigDict(extra="forbid")

    evidence_ids: list[int] = Field(default_factory=list)

    @field_validator("evidence_ids")
    @classmethod
    def _reject_duplicate_ids(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("evidence_ids must not contain duplicates")
        return value
