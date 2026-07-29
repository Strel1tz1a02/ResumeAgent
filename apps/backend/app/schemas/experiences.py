"""Pydantic contracts for the person-level experience library."""

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.evidence_items import EvidenceRead


class ExperienceKind(str, Enum):
    work = "work"
    internship = "internship"
    project = "project"
    research = "research"
    campus = "campus"
    volunteer = "volunteer"
    other = "other"


class ExperienceStatus(str, Enum):
    draft = "draft"
    ready = "ready"
    archived = "archived"


_YEAR_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MAX_IMPORT_TEXT_LENGTH = 20_000


def _normalize_labels(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return value  # type: ignore[return-value]

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            normalized.append(item)
            continue
        label = item.strip()
        key = label.casefold()
        if label and key not in seen:
            normalized.append(label)
            seen.add(key)
    return normalized


class _ExperienceWritable(BaseModel):
    """Editable fields shared by creates and patches."""

    kind: ExperienceKind | None = None
    title: str | None = None
    organization: str | None = None
    role: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool | None = None
    raw_input: str | None = None
    background: str | None = None
    technologies: list[str] | None = None
    tags: list[str] | None = None
    notes: str | None = None

    @field_validator("start_date", "end_date")
    @classmethod
    def _validate_year_month(cls, value: str | None) -> str | None:
        if value is not None and not _YEAR_MONTH_RE.fullmatch(value):
            raise ValueError("date must use YYYY-MM format")
        return value

    @field_validator("technologies", "tags", mode="before")
    @classmethod
    def _normalize_label_lists(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        return _normalize_labels(value)

    @model_validator(mode="after")
    def _current_experience_cannot_end(self) -> "_ExperienceWritable":
        if self.is_current is True and self.end_date is not None:
            raise ValueError("current experiences cannot have an end_date")
        return self


class ExperienceCreate(_ExperienceWritable):
    """Client-supplied fields for manually creating an experience."""

    model_config = ConfigDict(extra="forbid")

    kind: ExperienceKind = ExperienceKind.other
    title: str = ""
    is_current: bool = False
    raw_input: str = ""
    technologies: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ExperienceUpdate(_ExperienceWritable):
    """Client-supplied partial update; server-owned fields remain inaccessible."""

    model_config = ConfigDict(extra="forbid")


class ExperienceRead(BaseModel):
    """Stored experience fields returned to clients."""

    experience_id: int
    kind: ExperienceKind
    title: str
    organization: str | None = None
    role: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    raw_input: str = ""
    background: str | None = None
    evidence_ids: list[int] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    status: ExperienceStatus
    completeness: int = Field(ge=0, le=100)
    archived_at: str | None = None
    created_at: str
    updated_at: str


class ExperienceDetail(ExperienceRead):
    """An experience with expanded evidence and recomputed guidance."""

    evidence_items: list[EvidenceRead] = Field(default_factory=list)
    missing_dimensions: list[str] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)


class ExperienceListQuery(BaseModel):
    """Supported local-library filters and sort modes."""

    model_config = ConfigDict(extra="forbid")

    q: str | None = None
    kind: ExperienceKind | None = None
    status: Literal["active", "draft", "ready", "archived"] = "active"
    sort: Literal["updated_at_desc", "created_at_desc", "created_at_asc"] = "updated_at_desc"


class ExperienceListResponse(BaseModel):
    """List response shaped for later pagination without changing item contracts."""

    items: list[ExperienceRead] = Field(default_factory=list)
    total: int = Field(ge=0, default=0)


class ExperienceCompleteness(BaseModel):
    """Derived, non-persisted completeness guidance."""

    completeness: int = Field(ge=0, le=100)
    missing_dimensions: list[str] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)


class ExperienceImportTextRequest(BaseModel):
    """Raw user text persisted immediately before optional later enrichment."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_IMPORT_TEXT_LENGTH)

    @field_validator("text")
    @classmethod
    def _reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class ReadyConflictResponse(BaseModel):
    """Current score and missing facts returned when ready marking is rejected."""

    completeness: int = Field(ge=0, le=100)
    missing_dimensions: list[str] = Field(default_factory=list)


class DeletionImpactResponse(BaseModel):
    """Stable forward-compatible impact contract for permanent deletion review."""

    affected_matches: list[int] = Field(default_factory=list)
    affected_resumes: list[str] = Field(default_factory=list)
