"""简历生成模块的稳定输入、规划与输出契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai_chat.protocol import RunStatus
from app.schemas.models import ResumeData

Importance = Literal["must", "should", "nice"]
SearchIntent = Literal[
    "exact_skill",
    "responsibility",
    "scenario",
    "result_evidence",
    "transferable",
]
GenerationMode = Literal["auto", "llm", "deterministic"]
PlanAction = Literal[
    "search_more",
    "replace_experience",
    "add_evidence",
    "move_to_skill",
    "compress_section",
    "drop_redundant_content",
    "accept_with_gaps",
]


class ResumeConstraints(BaseModel):
    """限制组合规模和搜索循环的用户可调预算。"""

    model_config = ConfigDict(extra="forbid")

    page_count: Literal[1, 2] = 1
    max_work_experiences: int = Field(default=3, ge=0, le=8)
    max_project_experiences: int = Field(default=3, ge=0, le=8)
    max_bullets_per_experience: int = Field(default=3, ge=1, le=6)
    top_k_per_task: int = Field(default=12, ge=1, le=50)
    max_search_rounds: int = Field(default=2, ge=1, le=3)
    min_coverage_ratio: float = Field(default=0.75, ge=0, le=1)


class ResumeGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jd_information_id: int = Field(gt=0)
    mode: GenerationMode = "auto"
    constraints: ResumeConstraints = Field(default_factory=ResumeConstraints)


class JDRequirementSnapshot(BaseModel):
    id: int
    priority: Literal["required", "preferred", "normal"]
    content: str
    sort_order: int
    revision: int


class JDAnalysisSourceSnapshot(BaseModel):
    id: int
    source_url: str | None = None
    company: str
    job_name: str
    type: str
    location: str
    status: Literal["incomplete", "confirmed"]
    revision: int
    requirements: list[JDRequirementSnapshot]


class EvidenceSnapshot(BaseModel):
    evidence_id: int
    background: str | None = None
    action: str
    result: str | None = None
    updated_at: str


class ExperienceSnapshot(BaseModel):
    experience_id: int
    kind: str
    title: str
    organization: str | None = None
    role: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    background: str | None = None
    technologies: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    status: Literal["ready"] = "ready"
    completeness: int = Field(ge=0, le=100)
    updated_at: str
    evidence: list[EvidenceSnapshot] = Field(default_factory=list)


class CoverageItem(BaseModel):
    coverage_id: str
    source_requirement_ids: list[int] = Field(min_length=1)
    statement: str
    importance: Importance
    capability: str
    evidence_expectation: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)


class JDAnalysisSnapshot(BaseModel):
    source: JDAnalysisSourceSnapshot
    target_title: str
    coverage_items: list[CoverageItem]


class SearchTask(BaseModel):
    task_id: str
    coverage_item_ids: list[str] = Field(min_length=1)
    intent: SearchIntent
    query: str
    filters: dict[str, str] = Field(default_factory=lambda: {"status": "ready"})
    top_k: int = Field(default=12, ge=1, le=50)


class EvidenceDocument(BaseModel):
    evidence_id: int
    experience_id: int
    kind: str
    title: str
    organization: str | None = None
    role: str | None = None
    dates: str = ""
    experience_background: str | None = None
    evidence_background: str | None = None
    action: str
    result: str | None = None
    technologies: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    def searchable_text(self) -> str:
        values = [
            self.kind,
            self.title,
            self.organization,
            self.role,
            self.dates,
            self.experience_background,
            self.evidence_background,
            self.action,
            self.result,
            *self.technologies,
            *self.tags,
        ]
        return "\n".join(value for value in values if value)


class RetrievedEvidence(BaseModel):
    document: EvidenceDocument
    retrieval_score: float = Field(ge=0, le=1)
    matched_terms: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)


class EvidenceJudgment(BaseModel):
    evidence_id: int
    experience_id: int
    coverage_item_ids: list[str] = Field(default_factory=list)
    relevance: float = Field(ge=0, le=1)
    evidence_strength: float = Field(ge=0, le=1)
    uniqueness: float = Field(ge=0, le=1)
    supported_skills: list[str] = Field(default_factory=list)
    unsupported_risk: list[str] = Field(default_factory=list)
    reason: str = ""


class PlannedExperience(BaseModel):
    experience_id: int
    section: Literal["workExperience", "personalProjects"]
    evidence_ids: list[int] = Field(min_length=1)
    coverage_item_ids: list[str] = Field(default_factory=list)
    bullet_budget: int = Field(ge=1, le=6)
    score: float = 0
    reason: str = ""


class PromotedSkill(BaseModel):
    skill: str
    evidence_ids: list[int] = Field(min_length=1)
    coverage_item_ids: list[str] = Field(default_factory=list)
    reason: str


class OmittedCandidate(BaseModel):
    experience_id: int
    evidence_ids: list[int] = Field(default_factory=list)
    reason: str


class CoverageStatus(BaseModel):
    coverage_id: str
    importance: Importance
    covered: bool
    evidence_ids: list[int] = Field(default_factory=list)


class ResumePlan(BaseModel):
    version: Literal["1"] = "1"
    selected_experiences: list[PlannedExperience] = Field(default_factory=list)
    promoted_skills: list[PromotedSkill] = Field(default_factory=list)
    coverage: list[CoverageStatus] = Field(default_factory=list)
    uncovered_requirements: list[str] = Field(default_factory=list)
    omitted_candidates: list[OmittedCandidate] = Field(default_factory=list)
    search_rounds: int = Field(ge=1)
    coverage_ratio: float = Field(ge=0, le=1)
    review_actions: list[PlanAction] = Field(default_factory=list)
    review_warnings: list[str] = Field(default_factory=list)


class PlanCritique(BaseModel):
    acceptable: bool
    actions: list[PlanAction] = Field(default_factory=list)
    gap_coverage_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DraftBullet(BaseModel):
    experience_id: int
    evidence_ids: list[int] = Field(min_length=1)
    text: str


class DraftedExperience(BaseModel):
    experience_id: int
    bullets: list[DraftBullet] = Field(default_factory=list)


class ResumeDraft(BaseModel):
    summary: str = ""
    experiences: list[DraftedExperience] = Field(default_factory=list)


class BulletProvenance(BaseModel):
    section: Literal["workExperience", "personalProjects"]
    item_id: int
    bullet_index: int = Field(ge=0)
    evidence_ids: list[int] = Field(min_length=1)


class SkillProvenance(BaseModel):
    skill: str
    evidence_ids: list[int] = Field(min_length=1)


class ResumeProvenance(BaseModel):
    bullets: list[BulletProvenance] = Field(default_factory=list)
    skills: list[SkillProvenance] = Field(default_factory=list)


class ResumeValidation(BaseModel):
    valid: bool
    coverage_ratio: float = Field(ge=0, le=1)
    uncovered_requirements: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ResumeGenerationPreview(BaseModel):
    run_id: str
    status: Literal["completed"] = "completed"
    artifact_status: Literal["previewed"] = "previewed"
    plan: ResumePlan
    resume_data: ResumeData
    provenance: ResumeProvenance
    validation: ResumeValidation


class ResumeGenerationRunResponse(BaseModel):
    run_id: str
    status: RunStatus
    artifact_status: Literal["pending", "previewed", "confirmed"]
    jd_information_id: int
    request: ResumeGenerationRequest
    jd_snapshot: JDAnalysisSnapshot | None = None
    plan: ResumePlan | None = None
    resume_data: ResumeData | None = None
    provenance: ResumeProvenance | None = None
    validation: ResumeValidation | None = None
    resume_id: str | None = None
    error: str | None = None
    created_at: str
    updated_at: str


class ResumeGenerationConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def strip_title(self) -> ResumeGenerationConfirmRequest:
        if self.title is not None:
            self.title = self.title.strip() or None
        return self


class ResumeGenerationConfirmResponse(BaseModel):
    run_id: str
    status: Literal["completed"] = "completed"
    artifact_status: Literal["confirmed"] = "confirmed"
    resume_id: str
