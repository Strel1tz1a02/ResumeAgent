"""简历生成用例：快照、Graph、持久化预览和幂等确认。"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.graph import GraphDriver, LangGraphDriver
from app.ai_chat.protocol import GraphOutcome
from app.ai_chat.run_state import RunStateMachine
from app.ai_chat.streaming.events import RuntimeEvent
from app.config import settings
from app.experience.repositories.evidence_repository import EvidenceRepository
from app.experience.repositories.experience_repository import ExperienceRepository
from app.jd_import.repositories import JDImportRepository
from app.models import Resume
from app.resume_generation.graph import (
    ResumeGenerationGraphDependencies,
    build_resume_generation_graph,
)
from app.resume_generation.model import (
    FallbackResumeGenerationModel,
    LangChainResumeGenerationModel,
    ResumeGenerationModel,
    RuleBasedResumeGenerationModel,
)
from app.resume_generation.planner import render_resume_markdown
from app.resume_generation.repository import ResumeGenerationRepository, utcnow_iso
from app.resume_generation.retriever import EvidenceRetriever, QdrantEvidenceRetriever
from app.resume_generation.schemas import (
    EvidenceSnapshot,
    ExperienceSnapshot,
    JDAnalysisSnapshot,
    JDAnalysisSourceSnapshot,
    JDRequirementSnapshot,
    ResumeGenerationConfirmRequest,
    ResumeGenerationConfirmResponse,
    ResumeGenerationPreview,
    ResumeGenerationRequest,
    ResumeGenerationRunResponse,
    ResumePlan,
    ResumeProvenance,
    ResumeValidation,
)
from app.schemas.models import ResumeData


class ResumeGenerationError(Exception):
    pass


class ResumeGenerationNotFoundError(ResumeGenerationError):
    pass


class ResumeGenerationValidationError(ResumeGenerationError):
    pass


class ResumeGenerationConflictError(ResumeGenerationError):
    pass


class ResumeGenerationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        llm_model: ResumeGenerationModel | None = None,
        deterministic_model: ResumeGenerationModel | None = None,
        retriever: EvidenceRetriever | None = None,
        graph_driver: GraphDriver | None = None,
    ) -> None:
        self._session = session
        self._runs = ResumeGenerationRepository(session)
        self._jd = JDImportRepository(session)
        self._experiences = ExperienceRepository(session)
        self._evidence = EvidenceRepository(session)
        self._llm_model = llm_model or LangChainResumeGenerationModel()
        self._deterministic_model = (
            deterministic_model or RuleBasedResumeGenerationModel()
        )
        self._retriever = retriever or QdrantEvidenceRetriever(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            collection_name=settings.qdrant_collection,
            dense_model=settings.qdrant_dense_model,
            sparse_model=settings.qdrant_sparse_model,
            timeout_seconds=settings.qdrant_timeout_seconds,
        )
        self._graph_driver = graph_driver or LangGraphDriver()

    async def preview(
        self, request: ResumeGenerationRequest
    ) -> ResumeGenerationPreview:
        jd_source = await self._load_jd_snapshot(request.jd_information_id)
        experiences = await self._load_experience_snapshots()
        if not experiences:
            raise ResumeGenerationValidationError(
                "resume generation requires at least one ready experience with evidence"
            )
        run_id = str(uuid4())
        run = await self._runs.create(
            run_id=run_id,
            jd_information_id=request.jd_information_id,
            request_json=request.model_dump(mode="json"),
        )
        await self._session.commit()

        model = self._select_model(request.mode)
        graph = build_resume_generation_graph(
            ResumeGenerationGraphDependencies(model=model, retriever=self._retriever)
        ).compile()
        try:
            result: dict | None = None
            outcome: GraphOutcome | None = None
            async for item in self._graph_driver.stream(
                graph=graph,
                graph_input={
                    "run_id": run_id,
                    "jd_source": jd_source,
                    "experiences": experiences,
                    "constraints": request.constraints,
                },
            ):
                if isinstance(item, GraphOutcome):
                    outcome = item
                elif (
                    isinstance(item, RuntimeEvent)
                    and item.type == "result.available"
                    and item.payload.get("kind") == "resume_generation"
                    and isinstance(item.payload.get("result"), dict)
                ):
                    result = dict(item.payload["result"])
            if outcome != GraphOutcome.completed() or result is None:
                raise RuntimeError("resume generation Graph returned no completed result")
            analysis = JDAnalysisSnapshot.model_validate(result["analysis"])
            plan = ResumePlan.model_validate(result["plan"])
            resume_data = ResumeData.model_validate(result["resume_data"])
            provenance = ResumeProvenance.model_validate(result["provenance"])
            validation = ResumeValidation.model_validate(result["validation"])
            transitioned = await RunStateMachine(self._runs).transition(
                run_id,
                from_statuses={"running"},
                to_status="completed",
            )
            if not transitioned:
                raise RuntimeError("resume generation Run is no longer running")
            await self._runs.update(
                run,
                artifact_status="previewed",
                jd_snapshot_json=analysis.model_dump(mode="json"),
                experience_snapshots_json=[
                    item.model_dump(mode="json") for item in experiences
                ],
                plan_json=plan.model_dump(mode="json"),
                resume_data_json=resume_data.model_dump(mode="json"),
                provenance_json=provenance.model_dump(mode="json"),
                validation_json=validation.model_dump(mode="json"),
            )
            await self._session.commit()
            return ResumeGenerationPreview(
                run_id=run_id,
                plan=plan,
                resume_data=resume_data,
                provenance=provenance,
                validation=validation,
            )
        except Exception as error:
            await self._session.rollback()
            run = await self._runs.get(run_id)
            if run is not None:
                await RunStateMachine(self._runs).transition(
                    run_id,
                    from_statuses={"running"},
                    to_status="failed",
                    error_code=str(error),
                )
                await self._session.commit()
            raise ResumeGenerationError(f"resume generation failed: {error}") from error

    async def get(self, run_id: str) -> ResumeGenerationRunResponse:
        row = await self._runs.get(run_id)
        if row is None:
            raise ResumeGenerationNotFoundError(
                f"resume generation run {run_id} does not exist"
            )
        return self._read_run(row)

    async def confirm(
        self,
        run_id: str,
        request: ResumeGenerationConfirmRequest,
    ) -> ResumeGenerationConfirmResponse:
        row = await self._runs.get(run_id)
        if row is None:
            raise ResumeGenerationNotFoundError(
                f"resume generation run {run_id} does not exist"
            )
        if row.generated_resume_id:
            return ResumeGenerationConfirmResponse(
                run_id=run_id, resume_id=row.generated_resume_id
            )
        if row.status != "completed" or row.artifact_status != "previewed":
            raise ResumeGenerationConflictError(
                f"run {run_id} must be previewed before confirmation"
            )
        validation = ResumeValidation.model_validate(row.validation_json)
        if not validation.valid:
            raise ResumeGenerationConflictError(
                "run validation failed and cannot be confirmed"
            )
        resume_data = ResumeData.model_validate(row.resume_data_json)
        resume_id = f"resume-generation-{run_id}"
        existing = await self._session.get(Resume, resume_id)
        if existing is None:
            title = request.title or self._default_title(row)
            self._session.add(
                Resume(
                    resume_id=resume_id,
                    content=render_resume_markdown(resume_data),
                    content_type="md",
                    filename=None,
                    is_master=False,
                    parent_id=None,
                    processed_data=resume_data.model_dump(mode="json"),
                    processing_status="ready",
                    title=title,
                    created_at=utcnow_iso(),
                    updated_at=utcnow_iso(),
                )
            )
        await self._runs.update(
            row,
            artifact_status="confirmed",
            generated_resume_id=resume_id,
        )
        try:
            await self._session.commit()
        except IntegrityError:
            # resume_id 由 run_id 确定；并发确认或未知提交结果都收敛到同一主键。
            await self._session.rollback()
            recovered_resume = await self._session.get(Resume, resume_id)
            recovered_run = await self._runs.get(run_id)
            if recovered_resume is None or recovered_run is None:
                raise
            if recovered_run.generated_resume_id is None:
                await self._runs.update(
                    recovered_run,
                    artifact_status="confirmed",
                    generated_resume_id=resume_id,
                )
                await self._session.commit()
        return ResumeGenerationConfirmResponse(run_id=run_id, resume_id=resume_id)

    def _select_model(self, mode: str) -> ResumeGenerationModel:
        if mode == "deterministic":
            return self._deterministic_model
        if mode == "llm":
            return self._llm_model
        return FallbackResumeGenerationModel(self._llm_model, self._deterministic_model)

    async def _load_jd_snapshot(self, information_id: int) -> JDAnalysisSourceSnapshot:
        item = await self._jd.get(information_id)
        if item is None:
            raise ResumeGenerationNotFoundError(
                f"JD information {information_id} does not exist"
            )
        if not item.requirements:
            raise ResumeGenerationValidationError(
                "resume generation requires at least one JD requirement"
            )
        return JDAnalysisSourceSnapshot(
            id=item.id,
            source_url=item.source_url,
            company=item.company,
            job_name=item.job_name,
            type=item.type,
            location=item.location,
            status=item.status,
            revision=item.revision,
            requirements=[
                JDRequirementSnapshot(
                    id=requirement.id,
                    priority=requirement.priority,
                    content=requirement.content,
                    sort_order=requirement.sort_order,
                    revision=requirement.revision,
                )
                for requirement in item.requirements
            ],
        )

    async def _load_experience_snapshots(self) -> list[ExperienceSnapshot]:
        items = await self._experiences.list(status="ready")
        snapshots: list[ExperienceSnapshot] = []
        for item in items:
            evidence = await self._evidence.list_for_experience(item.experience_id)
            if not evidence:
                continue
            snapshots.append(
                ExperienceSnapshot(
                    experience_id=item.experience_id,
                    kind=item.kind,
                    title=item.title,
                    organization=item.organization,
                    role=item.role,
                    location=item.location,
                    start_date=item.start_date,
                    end_date=item.end_date,
                    is_current=item.is_current,
                    background=item.background,
                    technologies=list(item.technologies or []),
                    tags=list(item.tags or []),
                    status="ready",
                    completeness=item.completeness,
                    updated_at=item.updated_at,
                    evidence=[
                        EvidenceSnapshot(
                            evidence_id=value.id,
                            background=value.background,
                            action=value.action,
                            result=value.result,
                            updated_at=value.updated_at,
                        )
                        for value in evidence
                    ],
                )
            )
        return snapshots

    @staticmethod
    def _default_title(row) -> str:
        if row.jd_snapshot_json:
            source = row.jd_snapshot_json.get("source", {})
            company = source.get("company", "")
            job_name = source.get("job_name", "")
            label = " - ".join(part for part in (company, job_name) if part)
            if label:
                return f"定制简历 - {label}"
        return "定制简历"

    @staticmethod
    def _read_run(row) -> ResumeGenerationRunResponse:
        return ResumeGenerationRunResponse(
            run_id=row.run_id,
            status=row.status,
            artifact_status=row.artifact_status,
            jd_information_id=row.jd_information_id,
            request=ResumeGenerationRequest.model_validate(row.request_json),
            jd_snapshot=(
                JDAnalysisSnapshot.model_validate(row.jd_snapshot_json)
                if row.jd_snapshot_json
                else None
            ),
            plan=ResumePlan.model_validate(row.plan_json) if row.plan_json else None,
            resume_data=(
                ResumeData.model_validate(row.resume_data_json)
                if row.resume_data_json
                else None
            ),
            provenance=(
                ResumeProvenance.model_validate(row.provenance_json)
                if row.provenance_json
                else None
            ),
            validation=(
                ResumeValidation.model_validate(row.validation_json)
                if row.validation_json
                else None
            ),
            resume_id=row.generated_resume_id,
            error=row.error,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
