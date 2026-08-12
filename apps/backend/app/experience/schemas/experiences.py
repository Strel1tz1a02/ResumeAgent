"""个人经历库的 Pydantic 契约。"""

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.experience.schemas.evidence_items import EvidenceCreate, EvidenceRead


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


# BaseModel：Pydantic 提供的模型基类，用来定义带有类型检查、数据校验和转换能力的数据结构。
class _ExperienceWritable(BaseModel):
    """创建与局部更新共用的可编辑字段。"""

    kind: ExperienceKind | None = None
    title: str | None = None
    organization: str | None = None
    role: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool | None = None
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
    """客户端手动创建经历时提供的字段。"""

    model_config = ConfigDict(
        extra="forbid"
    )  # model_config：Pydantic配置的固定名称，extra="forbid"：如果输入数据包含模型中没有定义的额外字段，就直接校验失败。

    kind: ExperienceKind = ExperienceKind.other
    title: str = ""
    is_current: bool = False
    technologies: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ExperienceUpdate(_ExperienceWritable):
    """客户端提供的局部更新；服务端字段保持不可写。"""

    model_config = ConfigDict(extra="forbid")

    expected_field_revisions: dict[str, int] = Field(default_factory=dict)


class ExperienceEvidenceSave(EvidenceCreate):
    """全局保存中的 Evidence；有 ID 更新，无 ID 创建。"""

    model_config = ConfigDict(extra="forbid")

    evidence_id: int | None = Field(default=None, gt=0)
    expected_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _existing_evidence_requires_revision(self) -> "ExperienceEvidenceSave":
        if (self.evidence_id is None) != (self.expected_revision is None):
            raise ValueError(
                "existing evidence requires both evidence_id and expected_revision"
            )
        return self


class ExperienceGlobalSave(BaseModel):
    """聚合保存：有 experience_id 覆盖，无 experience_id 创建。"""

    model_config = ConfigDict(extra="forbid")

    experience_id: int | None = Field(default=None, gt=0)
    experience: ExperienceUpdate
    evidence_items: list[ExperienceEvidenceSave] = Field(default_factory=list)
    expected_collection_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_create_or_update_contract(self) -> "ExperienceGlobalSave":
        if self.experience_id is None:
            if self.expected_collection_revision is not None:
                raise ValueError(
                    "new experience must not provide a collection revision"
                )
            if self.experience.expected_field_revisions:
                raise ValueError("new experience must not provide field revisions")
            if any(item.evidence_id is not None for item in self.evidence_items):
                raise ValueError("new experience cannot reference existing evidence")
        elif self.expected_collection_revision is None:
            raise ValueError("existing experience requires a collection revision")
        return self


class ExperienceRead(BaseModel):
    """返回给客户端的已存储经历字段。"""

    experience_id: int
    kind: ExperienceKind
    title: str
    organization: str | None = None
    role: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
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
    """包含展开证据和重算引导信息的经历。"""

    evidence_items: list[EvidenceRead] = Field(default_factory=list)
    missing_dimensions: list[str] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)
    field_states: list["ExperienceFieldStateRead"] = Field(default_factory=list)


class ExperienceFieldStateRead(BaseModel):
    """返回给经历编辑器的一项字段状态和修订号。"""

    key: str
    ref_id: int | None = None
    status: Literal["complete", "incomplete"]
    revision: int = Field(ge=0)


class ExperienceListQuery(BaseModel):
    """本地经历库支持的筛选和排序方式。"""

    model_config = ConfigDict(extra="forbid")

    q: str | None = None
    kind: ExperienceKind | None = None
    status: Literal["active", "draft", "ready", "archived"] = "active"
    sort: Literal["updated_at_desc", "created_at_desc", "created_at_asc"] = (
        "updated_at_desc"
    )


class ExperienceListResponse(BaseModel):
    """为后续分页预留结构且不改变项目契约的列表响应。"""

    items: list[ExperienceRead] = Field(default_factory=list)
    total: int = Field(ge=0, default=0)


class ExperienceCompleteness(BaseModel):
    """派生且不持久化的完整度引导信息。"""

    completeness: int = Field(ge=0, le=100)
    missing_dimensions: list[str] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)


class ExperienceImportTextRequest(BaseModel):
    """仅用于一次结构化导入请求的临时源文本。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_IMPORT_TEXT_LENGTH)

    @field_validator("text")
    @classmethod
    def _reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class ReadyConflictResponse(BaseModel):
    """标记就绪被拒绝时返回的当前分数和缺失事实。"""

    completeness: int = Field(ge=0, le=100)
    missing_dimensions: list[str] = Field(default_factory=list)


class DeletionImpactMatch(BaseModel):
    """受永久删除影响的一条未来匹配记录。"""

    match_id: int
    job_title: str


class DeletionImpactResponse(BaseModel):
    """用于永久删除审查的稳定、向前兼容影响契约。"""

    affected_matches: list[DeletionImpactMatch] = Field(default_factory=list)
    affected_resumes: list[str] = Field(default_factory=list)
