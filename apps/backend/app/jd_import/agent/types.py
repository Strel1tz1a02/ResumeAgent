"""JD 导入提取与补问使用的可序列化领域类型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceType = Literal["text", "url", "user_answer"]
UrlStatus = Literal["skipped", "fetched", "blocked", "failed"]
ConflictKind = Literal["ownership", "missing", "conflict", "source_access"]
RequirementPriority = Literal["required", "preferred", "normal"]


class ImportSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    type: SourceType
    content: str = ""
    source_url: str | None = None
    url_status: UrlStatus | None = None


class ParsedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_input: str
    text: str
    urls: list[str]
    sources: list[ImportSource]


class EvidenceFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    source_id: str
    quote: str


class RequirementFact(EvidenceFact):
    priority: RequirementPriority = "normal"
    sort_order: int = Field(default=0, ge=0)


class CandidateJD(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jd_key: str
    source_url: EvidenceFact | None = None
    company: EvidenceFact | None = None
    job_name: EvidenceFact | None = None
    type: EvidenceFact | None = None
    location: EvidenceFact | None = None
    requirements: list[RequirementFact] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


class Conflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_key: str
    kind: ConflictKind
    target_jd_keys: list[str] = Field(default_factory=list)
    field: str
    values: list[str] = Field(default_factory=list)
    required: bool = False


class AssessmentError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["unknown_source", "unsupported_fact"]
    jd_key: str
    field: str


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[CandidateJD]
    conflicts: list[Conflict]
    errors: list[AssessmentError] = Field(default_factory=list)


class QuestionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_key: str
    prompt: str
    mode: Literal["choice", "text"] = "text"
    options: list[str] = Field(default_factory=list)


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    question_key: str
    kind: ConflictKind
    target_jd_keys: list[str] = Field(default_factory=list)
    field: str
    prompt: str
    mode: Literal["choice", "text"]
    options: list[str] = Field(default_factory=list)
    allow_custom: bool = True


class QuestionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str
    round: int = Field(ge=1, le=3)
    questions: list[Question] = Field(min_length=1, max_length=12)


class QuestionAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    value: str | None = None
    skipped: bool = False

    @model_validator(mode="after")
    def validate_value_or_skip(self) -> QuestionAnswer:
        if self.skipped:
            if self.value not in (None, ""):
                raise ValueError("skipped answer must not include a value")
            return self
        if self.value is None or not self.value.strip():
            raise ValueError("answer value must not be blank")
        self.value = self.value.strip()
        return self


class QuestionBatchAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["question_batch_answer"] = "question_batch_answer"
    batch_id: str
    client_resolution_id: str = Field(min_length=1, max_length=200)
    answers: list[QuestionAnswer]


class ImportErrorItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    jd_key: str | None = None


class ImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persisted_ids: list[int] = Field(default_factory=list)
    errors: list[ImportErrorItem] = Field(default_factory=list)
