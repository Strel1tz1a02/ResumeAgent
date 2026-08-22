"""简历规划语义模型协议、LangChain 实现与确定性降级实现。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from app.ai_chat.context import ContextAssembler
from app.llm import complete_json
from app.resume_generation.planner import critique_plan
from app.resume_generation.retriever import tokenize
from app.resume_generation.schemas import (
    CoverageItem,
    DraftBullet,
    DraftedExperience,
    EvidenceJudgment,
    ExperienceSnapshot,
    JDAnalysisSnapshot,
    JDAnalysisSourceSnapshot,
    PlanCritique,
    ResumeConstraints,
    ResumeDraft,
    ResumePlan,
    RetrievedEvidence,
    SearchTask,
)

Completion = Callable[..., Awaitable[dict[str, Any]]]
ResponseT = TypeVar("ResponseT", bound=BaseModel)

SYSTEM_PROMPT = """你是简历经历规划器。只能使用输入 JSON 中存在的事实和 ID。
不得补充数字、规模、职责、技能熟练度或结果。输出必须满足给定 JSON 结构。"""


class CoverageAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    coverage_items: list[CoverageItem]


class SearchPlanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[SearchTask]


class JudgmentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    judgments: list[EvidenceJudgment]


class ResumeGenerationModel(Protocol):
    async def analyze_jd(
        self, source: JDAnalysisSourceSnapshot
    ) -> list[CoverageItem]: ...

    async def plan_search(
        self,
        analysis: JDAnalysisSnapshot,
        *,
        gap_coverage_ids: list[str],
        search_round: int,
        top_k: int,
    ) -> list[SearchTask]: ...

    async def judge(
        self,
        analysis: JDAnalysisSnapshot,
        tasks: list[SearchTask],
        candidates: list[RetrievedEvidence],
    ) -> list[EvidenceJudgment]: ...

    async def critique(
        self,
        analysis: JDAnalysisSnapshot,
        plan: ResumePlan,
        judgments: list[EvidenceJudgment],
        constraints: ResumeConstraints,
        *,
        search_round: int,
        has_new_candidates: bool,
    ) -> PlanCritique: ...

    async def draft(
        self,
        analysis: JDAnalysisSnapshot,
        plan: ResumePlan,
        experiences: list[ExperienceSnapshot],
    ) -> ResumeDraft: ...


def _importance(priority: str) -> str:
    return {"required": "must", "preferred": "should", "normal": "nice"}[priority]


def _unique_labels(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        label = value.strip()
        key = label.casefold()
        if label and key not in seen:
            result.append(label)
            seen.add(key)
    return result


class RuleBasedResumeGenerationModel:
    """无模型配置时仍能完成全链路的保守实现。"""

    async def analyze_jd(self, source: JDAnalysisSourceSnapshot) -> list[CoverageItem]:
        return [
            CoverageItem(
                coverage_id=f"requirement-{item.id}",
                source_requirement_ids=[item.id],
                statement=item.content,
                importance=_importance(item.priority),
                capability=item.content,
                evidence_expectation=["实际行动", "具体结果"],
                aliases=sorted(
                    tokenize(item.content), key=lambda value: (-len(value), value)
                )[:12],
            )
            for item in sorted(
                source.requirements, key=lambda value: (value.sort_order, value.id)
            )
        ]

    async def plan_search(
        self,
        analysis: JDAnalysisSnapshot,
        *,
        gap_coverage_ids: list[str],
        search_round: int,
        top_k: int,
    ) -> list[SearchTask]:
        targets = [
            item
            for item in analysis.coverage_items
            if not gap_coverage_ids or item.coverage_id in gap_coverage_ids
        ]
        tasks: list[SearchTask] = []
        for item in targets:
            query_parts = [item.statement, item.capability]
            if search_round > 1:
                query_parts.extend(item.aliases)
                query_parts.extend(item.evidence_expectation)
            query = " ".join(_unique_labels(query_parts))
            tasks.append(
                SearchTask(
                    task_id=f"round-{search_round}:{item.coverage_id}:capability",
                    coverage_item_ids=[item.coverage_id],
                    intent="exact_skill" if search_round == 1 else "transferable",
                    query=query,
                    top_k=top_k,
                )
            )
            if search_round == 1:
                tasks.append(
                    SearchTask(
                        task_id=f"round-{search_round}:{item.coverage_id}:result",
                        coverage_item_ids=[item.coverage_id],
                        intent="result_evidence",
                        query=f"{query} 结果 影响 优化",
                        top_k=top_k,
                    )
                )
        return tasks

    async def judge(
        self,
        analysis: JDAnalysisSnapshot,
        tasks: list[SearchTask],
        candidates: list[RetrievedEvidence],
    ) -> list[EvidenceJudgment]:
        task_map = {task.task_id: task for task in tasks}
        coverage_map = {item.coverage_id: item for item in analysis.coverage_items}
        valid_coverage = set(coverage_map)
        judgments: list[EvidenceJudgment] = []
        for candidate in candidates:
            coverage_ids: list[str] = []
            for task_id in candidate.task_ids:
                task = task_map.get(task_id)
                if task is None:
                    continue
                coverage_ids.extend(
                    coverage_id
                    for coverage_id in task.coverage_item_ids
                    if coverage_id in valid_coverage
                )
            document = candidate.document
            document_terms = tokenize(document.searchable_text())
            # 检索任务会附带“结果/影响”等宽化词，不能仅因这些通用词命中就宣称
            # 覆盖。确定性 Judge 至少要求原 requirement/capability/alias 的一个
            # 有意义词项真实出现在 Evidence 文档中。
            supported_coverage: list[str] = []
            for coverage_id in sorted(set(coverage_ids)):
                coverage = coverage_map[coverage_id]
                source_terms = tokenize(
                    " ".join(
                        [coverage.statement, coverage.capability, *coverage.aliases]
                    )
                )
                meaningful = {term for term in source_terms if len(term) >= 2}
                named_latin = {
                    term
                    for term in meaningful
                    if term[0].isascii() and term[0].isalnum()
                }
                if named_latin:
                    is_supported = bool(named_latin & document_terms)
                else:
                    is_supported = bool(meaningful & document_terms)
                if is_supported:
                    supported_coverage.append(coverage_id)
            coverage_ids = supported_coverage
            strength = 0.45
            if document.evidence_background:
                strength += 0.1
            if document.result:
                strength += 0.25
            if any(char.isdigit() for char in (document.result or "")):
                strength += 0.1
            supported_skills = _unique_labels(document.technologies + document.tags)
            judgments.append(
                EvidenceJudgment(
                    evidence_id=document.evidence_id,
                    experience_id=document.experience_id,
                    coverage_item_ids=coverage_ids,
                    relevance=min(1.0, 0.35 + candidate.retrieval_score * 0.65),
                    evidence_strength=min(1.0, strength),
                    uniqueness=0.6 if len(coverage_ids) > 1 else 0.45,
                    supported_skills=supported_skills,
                    reason="由 Qdrant 混合召回命中且存在可追溯 Evidence",
                )
            )
        return judgments

    async def critique(
        self,
        analysis: JDAnalysisSnapshot,
        plan: ResumePlan,
        judgments: list[EvidenceJudgment],
        constraints: ResumeConstraints,
        *,
        search_round: int,
        has_new_candidates: bool,
    ) -> PlanCritique:
        """确定性降级才使用固定覆盖规则判断是否继续搜索。"""
        return critique_plan(
            plan,
            analysis,
            constraints,
            has_new_candidates=has_new_candidates,
        )

    async def draft(
        self,
        analysis: JDAnalysisSnapshot,
        plan: ResumePlan,
        experiences: list[ExperienceSnapshot],
    ) -> ResumeDraft:
        experience_map = {item.experience_id: item for item in experiences}
        drafted: list[DraftedExperience] = []
        for selected in plan.selected_experiences:
            experience = experience_map[selected.experience_id]
            evidence_map = {item.evidence_id: item for item in experience.evidence}
            bullets: list[DraftBullet] = []
            for evidence_id in selected.evidence_ids[: selected.bullet_budget]:
                evidence = evidence_map[evidence_id]
                text = evidence.action.strip()
                if evidence.result and evidence.result.strip():
                    result = evidence.result.strip()
                    if result not in text:
                        text = f"{text}；{result}"
                bullets.append(
                    DraftBullet(
                        experience_id=experience.experience_id,
                        evidence_ids=[evidence_id],
                        text=text,
                    )
                )
            drafted.append(
                DraftedExperience(
                    experience_id=experience.experience_id, bullets=bullets
                )
            )
        top_capabilities = [
            item.capability
            for item in analysis.coverage_items
            if any(
                status.coverage_id == item.coverage_id and status.covered
                for status in plan.coverage
            )
        ][:3]
        summary = "、".join(top_capabilities)
        if summary:
            summary = f"具备{summary}相关的实践经历。"
        return ResumeDraft(summary=summary, experiences=drafted)


class LangChainResumeGenerationModel:
    """使用 LangChain JSON 链路进行分析、判定和忠实改写。"""

    def __init__(self, completion: Completion = complete_json) -> None:
        self._completion = completion

    async def _structured(
        self,
        instruction: str,
        payload: dict[str, Any],
        response_type: type[ResponseT],
    ) -> ResponseT:
        context = ContextAssembler.assemble_structured(
            instructions=f"{SYSTEM_PROMPT}\n{instruction}",
            domain_sections=[
                {"name": "resume_generation_input", "data": payload}
            ],
        )
        try:
            result = await self._completion(
                context.prompt,
                system_prompt=context.system_prompt,
                retries=0,
                schema_type="resume_generation",
            )
            return response_type.model_validate(result)
        except ValidationError as error:
            result = await self._completion(
                f"{context.prompt}\n\n上次输出未通过校验，请只修复结构：{error.json()}",
                system_prompt=context.system_prompt,
                retries=0,
                schema_type="resume_generation",
            )
            return response_type.model_validate(result)

    async def analyze_jd(self, source: JDAnalysisSourceSnapshot) -> list[CoverageItem]:
        result = await self._structured(
            "将每条岗位要求拆成可独立验证的原子覆盖项。coverage_id 必须稳定且唯一，"
            "source_requirement_ids 只能引用输入 ID；required/preferred/normal 分别映射"
            "为 must/should/nice。不要丢失要求。",
            source.model_dump(mode="json"),
            CoverageAnalysisResult,
        )
        allowed = {item.id for item in source.requirements}
        if not result.coverage_items:
            raise ValueError("JD analysis returned no coverage items")
        if len({item.coverage_id for item in result.coverage_items}) != len(
            result.coverage_items
        ):
            raise ValueError("JD analysis returned duplicate coverage_id")
        if any(
            set(item.source_requirement_ids) - allowed for item in result.coverage_items
        ):
            raise ValueError("JD analysis referenced unknown requirement")
        covered_source_ids = {
            source_id
            for item in result.coverage_items
            for source_id in item.source_requirement_ids
        }
        if covered_source_ids != allowed:
            raise ValueError("JD analysis omitted one or more source requirements")
        return result.coverage_items

    async def plan_search(
        self,
        analysis: JDAnalysisSnapshot,
        *,
        gap_coverage_ids: list[str],
        search_round: int,
        top_k: int,
    ) -> list[SearchTask]:
        result = await self._structured(
            "为目标覆盖项生成精确技能、职责、场景、结果或迁移能力检索任务。只引用输入"
            "coverage_id；每项 query 应可直接检索经历证据。",
            {
                "analysis": analysis.model_dump(mode="json"),
                "gap_coverage_ids": gap_coverage_ids,
                "search_round": search_round,
                "top_k": top_k,
            },
            SearchPlanResult,
        )
        allowed = {item.coverage_id for item in analysis.coverage_items}
        if not result.tasks or any(
            set(item.coverage_item_ids) - allowed for item in result.tasks
        ):
            raise ValueError("search plan is empty or references unknown coverage")
        target = set(gap_coverage_ids) if gap_coverage_ids else allowed
        planned = {
            coverage_id
            for task in result.tasks
            for coverage_id in task.coverage_item_ids
        }
        if planned != target:
            raise ValueError(
                "search plan must cover exactly the requested coverage set"
            )
        for task in result.tasks:
            task.top_k = top_k
        return result.tasks

    async def judge(
        self,
        analysis: JDAnalysisSnapshot,
        tasks: list[SearchTask],
        candidates: list[RetrievedEvidence],
    ) -> list[EvidenceJudgment]:
        result = await self._structured(
            "逐条判断候选 Evidence 对 JD 覆盖项的真实支持程度。不要因为关键词出现就把"
            "弱证据判为强证据；supported_skills 只能来自候选内容。每个候选必须输出一次。",
            {
                "coverage_items": [
                    item.model_dump(mode="json") for item in analysis.coverage_items
                ],
                "tasks": [item.model_dump(mode="json") for item in tasks],
                "candidates": [item.model_dump(mode="json") for item in candidates],
            },
            JudgmentResult,
        )
        candidates_by_id = {
            item.document.evidence_id: item.document.experience_id
            for item in candidates
        }
        allowed_coverage = {item.coverage_id for item in analysis.coverage_items}
        if {item.evidence_id for item in result.judgments} != set(candidates_by_id):
            raise ValueError("judge must return every candidate exactly once")
        for item in result.judgments:
            if item.experience_id != candidates_by_id[item.evidence_id]:
                raise ValueError("judge changed evidence ownership")
            if set(item.coverage_item_ids) - allowed_coverage:
                raise ValueError("judge referenced unknown coverage")
            document = next(
                candidate.document
                for candidate in candidates
                if candidate.document.evidence_id == item.evidence_id
            )
            source_text = document.searchable_text().casefold()
            source_labels = {
                label.casefold() for label in document.technologies + document.tags
            }
            if any(
                skill.casefold() not in source_labels
                and skill.casefold() not in source_text
                for skill in item.supported_skills
            ):
                raise ValueError("judge returned a skill absent from source evidence")
        return result.judgments

    async def critique(
        self,
        analysis: JDAnalysisSnapshot,
        plan: ResumePlan,
        judgments: list[EvidenceJudgment],
        constraints: ResumeConstraints,
        *,
        search_round: int,
        has_new_candidates: bool,
    ) -> PlanCritique:
        """由模型结合证据质量定义真实空缺，并决定是否重规划。"""
        result = await self._structured(
            "评审当前简历计划是否已有足够、直接、具体的事实证据支持目标岗位。"
            "gap_coverage_ids 表示仍缺少可靠简历证据的覆盖项；不要仅依据 covered 布尔值，"
            "要综合 Evidence Judgment 的相关性、强度、风险和当前组合。你可以接受仍有空缺"
            "的计划，也可以在有合理搜索空间时选择 search_more。轮次、候选变化和预算只是"
            "提供给你的事实。不得引用输入外的 coverage_id。",
            {
                "analysis": analysis.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
                "judgments": [item.model_dump(mode="json") for item in judgments],
                "constraints": constraints.model_dump(mode="json"),
                "search_round": search_round,
                "has_new_candidates": has_new_candidates,
            },
            PlanCritique,
        )
        allowed = {item.coverage_id for item in analysis.coverage_items}
        if set(result.gap_coverage_ids) - allowed:
            raise ValueError("critique referenced unknown coverage")
        wants_more = "search_more" in result.actions
        if wants_more and not result.gap_coverage_ids:
            raise ValueError("search_more requires at least one evidence gap")
        if wants_more and search_round >= constraints.max_search_rounds:
            raise ValueError("critique exceeded the hard search round limit")
        if wants_more and search_round > 1 and not has_new_candidates:
            raise ValueError("critique requested search without new candidate progress")
        if wants_more == result.acceptable:
            raise ValueError("critique decision is internally inconsistent")
        return result

    async def draft(
        self,
        analysis: JDAnalysisSnapshot,
        plan: ResumePlan,
        experiences: list[ExperienceSnapshot],
    ) -> ResumeDraft:
        selected = {item.experience_id: item for item in plan.selected_experiences}
        evidence_payload = []
        for experience in experiences:
            if experience.experience_id not in selected:
                continue
            allowed = set(selected[experience.experience_id].evidence_ids)
            payload = experience.model_dump(mode="json")
            payload["evidence"] = [
                item.model_dump(mode="json")
                for item in experience.evidence
                if item.evidence_id in allowed
            ]
            evidence_payload.append(payload)
        result = await self._structured(
            "只用选中 Evidence 忠实压缩为简历 bullet。每个 bullet 必须列出实际使用的"
            "evidence_ids，不得引用未选证据，不得新增指标、技能或职责。",
            {
                "target_title": analysis.target_title,
                "plan": plan.model_dump(mode="json"),
                "selected_experiences": evidence_payload,
            },
            ResumeDraft,
        )
        allowed_by_experience = {
            item.experience_id: set(item.evidence_ids)
            for item in plan.selected_experiences
        }
        if {item.experience_id for item in result.experiences} != set(
            allowed_by_experience
        ):
            raise ValueError("draft changed selected experience set")
        for experience in result.experiences:
            for bullet in experience.bullets:
                if bullet.experience_id != experience.experience_id:
                    raise ValueError("draft bullet changed experience ownership")
                if (
                    not set(bullet.evidence_ids)
                    <= allowed_by_experience[experience.experience_id]
                ):
                    raise ValueError("draft referenced unplanned evidence")
        return result


class FallbackResumeGenerationModel:
    """auto 模式下逐阶段回退，避免一次模型失败破坏整个闭环。"""

    def __init__(
        self,
        primary: ResumeGenerationModel,
        fallback: ResumeGenerationModel,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.fallback_events: list[str] = []

    async def _call(self, name: str, *args: Any, **kwargs: Any):
        try:
            return await getattr(self.primary, name)(*args, **kwargs)
        except Exception:  # noqa: BLE001 - auto 模式必须在任意模型故障时确定性降级
            self.fallback_events.append(name)
            return await getattr(self.fallback, name)(*args, **kwargs)

    async def analyze_jd(self, source: JDAnalysisSourceSnapshot) -> list[CoverageItem]:
        return await self._call("analyze_jd", source)

    async def plan_search(
        self, analysis: JDAnalysisSnapshot, **kwargs: Any
    ) -> list[SearchTask]:
        return await self._call("plan_search", analysis, **kwargs)

    async def judge(
        self,
        analysis: JDAnalysisSnapshot,
        tasks: list[SearchTask],
        candidates: list[RetrievedEvidence],
    ) -> list[EvidenceJudgment]:
        return await self._call("judge", analysis, tasks, candidates)

    async def critique(
        self,
        analysis: JDAnalysisSnapshot,
        plan: ResumePlan,
        judgments: list[EvidenceJudgment],
        constraints: ResumeConstraints,
        **kwargs: Any,
    ) -> PlanCritique:
        return await self._call(
            "critique", analysis, plan, judgments, constraints, **kwargs
        )

    async def draft(
        self,
        analysis: JDAnalysisSnapshot,
        plan: ResumePlan,
        experiences: list[ExperienceSnapshot],
    ) -> ResumeDraft:
        return await self._call("draft", analysis, plan, experiences)
