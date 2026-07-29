"""Pydantic contracts for structured experience evidence."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EvidenceCreate(BaseModel):
    """Client-supplied fields for a new evidence record."""

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
    """Client-supplied partial update for one evidence record."""

    model_config = ConfigDict(extra="forbid")

    action: str | None = None
    result: str | None = None
    metrics: str | None = None

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
    """A persisted evidence record returned to clients."""

    id: int
    action: str
    result: str | None = None
    metrics: str | None = None
    created_at: str
    updated_at: str


class EvidenceReorder(BaseModel):
    """Requested presentation order; services verify it matches the current ID set."""

    model_config = ConfigDict(extra="forbid")

    evidence_ids: list[int] = Field(default_factory=list)

    @field_validator("evidence_ids")
    @classmethod
    def _reject_duplicate_ids(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("evidence_ids must not contain duplicates")
        return value
