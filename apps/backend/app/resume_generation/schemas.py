"""简历生成模块的稳定输入、规划与输出契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class DraftBullet(BaseModel):
    experience_id: int
    evidence_ids: list[int] = Field(min_length=1)
    text: str

    @field_validator("text")
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        """Draft 是拼装前的稳定产物，不允许后续再静默过滤空 Bullet。"""
        text = value.strip()
        if not text:
            raise ValueError("draft bullet text cannot be empty")
        return text


class DraftedExperience(BaseModel):
    experience_id: int
    bullets: list[DraftBullet] = Field(default_factory=list)


class ResumeDraft(BaseModel):
    summary: str = ""
    summary_evidence_ids: list[int] = Field(default_factory=list)
    experiences: list[DraftedExperience] = Field(default_factory=list)


FactVerdict = Literal["supported", "partial", "unsupported", "contradicted"]
ValidationClaimKind = Literal["summary", "bullet"]
ValidationCheckSource = Literal["reference", "model"]
ValidationCheckStatus = Literal["passed", "failed", "skipped"]


class DraftEvidenceQuote(BaseModel):
    """模型事实校验可见的单条引用原文，不混入 JD 或经历标签。"""

    evidence_id: int
    background: str | None = None
    action: str
    result: str | None = None


class DraftValidationClaim(BaseModel):
    """待校验的单条 Summary 或 Bullet 及其原始 Evidence。"""

    claim_id: str
    kind: ValidationClaimKind
    text: str
    experience_id: int | None = None
    bullet_index: int | None = Field(default=None, ge=0)
    evidence_ids: list[int] = Field(default_factory=list)
    evidence: list[DraftEvidenceQuote] = Field(default_factory=list)


class DraftClaimAssessment(BaseModel):
    """模型对单条简历陈述的事实支持判断。"""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    verdict: FactVerdict
    unsupported_fragments: list[str] = Field(default_factory=list)
    reason: str = ""

    @model_validator(mode="after")
    def validate_supported_fragments(self) -> DraftClaimAssessment:
        """避免模型一边判定完全支持，一边又报告不受支持片段。"""
        if self.verdict == "supported" and self.unsupported_fragments:
            raise ValueError(
                "supported assessment cannot contain unsupported fragments"
            )
        return self


class DraftFactCheckResponse(BaseModel):
    """模型必须严格返回的事实校验结构。"""

    model_config = ConfigDict(extra="forbid")

    assessments: list[DraftClaimAssessment] = Field(default_factory=list)


class DraftFactCheckResult(DraftFactCheckResponse):
    """事实校验运行结果；运行元数据由服务端填写。"""

    model_used: bool = True
    model_required: bool = True
    warnings: list[str] = Field(default_factory=list)


class ResumeValidationCheck(BaseModel):
    """持久化的逐条校验结果，便于按 Claim 和 Evidence 追溯。"""

    source: ValidationCheckSource
    status: ValidationCheckStatus
    claim_id: str
    kind: ValidationClaimKind
    experience_id: int | None = None
    bullet_index: int | None = Field(default=None, ge=0)
    evidence_ids: list[int] = Field(default_factory=list)
    verdict: FactVerdict | None = None
    unsupported_fragments: list[str] = Field(default_factory=list)
    message: str = ""


class BulletProvenance(BaseModel):
    section: Literal["workExperience", "personalProjects"]
    item_id: int
    bullet_index: int = Field(ge=0)
    evidence_ids: list[int] = Field(min_length=1)


class SkillProvenance(BaseModel):
    skill: str
    evidence_ids: list[int] = Field(min_length=1)


class ResumeProvenance(BaseModel):
    summary_evidence_ids: list[int] = Field(default_factory=list)
    bullets: list[BulletProvenance] = Field(default_factory=list)
    skills: list[SkillProvenance] = Field(default_factory=list)


class ResumeValidation(BaseModel):
    valid: bool
    coverage_ratio: float = Field(ge=0, le=1)
    uncovered_requirements: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    model_validation_status: Literal["completed", "skipped", "failed"] = "skipped"
    checks: list[ResumeValidationCheck] = Field(default_factory=list)


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
