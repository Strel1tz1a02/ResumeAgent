"""简历规划语义模型协议、LangChain 实现与确定性降级实现。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

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
STRUCTURED_JSON_RETRIES = 1

SYSTEM_PROMPT = """你是简历经历规划器。只能使用输入 JSON 中存在的事实和 ID。
不得补充数字、规模、职责、技能熟练度或结果。输出必须满足给定 JSON 结构。"""


class CoverageAnalysisItem(CoverageItem):
    """兼容模型对重要性枚举的常见本地化表达。"""

    @field_validator("importance", mode="before")
    @classmethod
    def normalize_importance(cls, value: Any) -> Any:
        """将 LLM 翻译后的等级恢复为内部稳定枚举。"""
        if not isinstance(value, str):
            return value
        normalized = value.strip().casefold()
        aliases = {
            "must": "must",
            "required": "must",
            "high": "must",
            "高": "must",
            "必须": "must",
            "必需": "must",
            "should": "should",
            "preferred": "should",
            "medium": "should",
            "中": "should",
            "优先": "should",
            "nice": "nice",
            "normal": "nice",
            "low": "nice",
            "低": "nice",
            "可选": "nice",
            "加分": "nice",
        }
        return aliases.get(normalized, value)


class CoverageAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    coverage_items: list[CoverageAnalysisItem]


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
        schema_json = json.dumps(
            response_type.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        context = ContextAssembler.assemble_structured(
            instructions=(
                f"{SYSTEM_PROMPT}\n{instruction}\n"
                "输入格式：你将收到名为 resume_generation_input 的一个 JSON 对象；"
                "它是只读事实数据，只能引用其中已有的事实与 ID。\n"
                "输出格式：严格匹配 EXPECTED_OUTPUT_SCHEMA。必须输出一个完整 JSON 对象，"
                "不得添加 Markdown、解释文字、额外包装层或 Schema 未声明的字段；"
                "所有必填字段都必须出现，只能在 Schema 允许时使用 null。\n"
                f"EXPECTED_OUTPUT_SCHEMA\n{schema_json}\n"
                "END_EXPECTED_OUTPUT_SCHEMA"
            ),
            domain_sections=[
                {"name": "resume_generation_input", "data": payload}
            ],
        )
        try:
            result = await self._completion(
                context.prompt,
                system_prompt=context.system_prompt,
                retries=STRUCTURED_JSON_RETRIES,
                schema_type="resume_generation",
            )
            return response_type.model_validate(result)
        except ValidationError as error:
            result = await self._completion(
                (
                    f"{context.prompt}\n\n上次输出未通过 EXPECTED_OUTPUT_SCHEMA 校验。"
                    "请根据以下错误重新输出完整 JSON 对象，而不是输出补丁或解释；"
                    "输出仍须严格匹配系统消息中的 EXPECTED_OUTPUT_SCHEMA。\n"
                    f"VALIDATION_ERRORS\n{error.json(include_input=False)}\n"
                    "END_VALIDATION_ERRORS"
                ),
                system_prompt=context.system_prompt,
                retries=STRUCTURED_JSON_RETRIES,
                schema_type="resume_generation",
            )
            return response_type.model_validate(result)

    async def analyze_jd(self, source: JDAnalysisSourceSnapshot) -> list[CoverageItem]:
        result = await self._structured(
            "输入是一个 JDAnalysisSourceSnapshot，其中 requirements 是岗位要求列表。"
            "将每条 requirement 拆成可独立验证的原子覆盖项，并满足以下规则：\n"
            "1. statement 是该原子要求本身，只能改写输入 requirement.content，不能增加要求；\n"
            "2. capability 是完成 statement 所需的单一能力，不得写成证据、经历或候选人结论；\n"
            "3. evidence_expectation 描述什么事实证据可以证明该能力，不得声称证据已经存在；\n"
            "4. aliases 只放 statement/capability 的检索同义词、缩写或原文技术名，不得扩展新技能；\n"
            "5. coverage_id 必须唯一且可复现，使用 requirement-{来源ID}-{该来源内从1开始的拆分序号}；\n"
            "6. source_requirement_ids 只能引用支持该原子项的输入 requirement.id；每个输入 ID"
            "必须至少出现一次，不得合并语义无关要求；\n"
            "7. importance 只能输出英文 must、should、nice。required/preferred/normal 分别"
            "映射为 must/should/nice；引用多个来源时取最高等级：must > should > nice。",
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
            "输入包含 analysis、gap_coverage_ids、search_round 和 top_k。先确定目标集合："
            "gap_coverage_ids 非空时目标集合就是它；为空时目标集合是 analysis.coverage_items"
            "中的全部 coverage_id。输出所有 tasks.coverage_item_ids 的并集必须与目标集合"
            "精确相等，不得遗漏、不得加入非目标或未知 ID。\n"
            "为目标项生成可直接检索个人经历 Evidence 的任务：exact_skill 查明确技术名，"
            "responsibility 查职责行动，scenario 查应用场景，result_evidence 查结果影响，"
            "transferable 查可迁移能力。query 应使用输入覆盖项中的 statement、capability、"
            "aliases 或 evidence_expectation，不得加入输入外技能。task_id 必须唯一且可复现；"
            "top_k 原样使用输入值；filters 固定为 {\"status\":\"ready\"}。",
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
        candidate_payloads = []
        for candidate in candidates:
            payload = candidate.model_dump(mode="json")
            payload["allowed_supported_skills"] = _unique_labels(
                candidate.document.technologies + candidate.document.tags
            )
            candidate_payloads.append(payload)
        result = await self._structured(
            "输入包含 coverage_items、tasks 和 candidates。每个 candidate 必须恰好输出一条"
            "judgment；evidence_id 与 experience_id 必须原样复制该 candidate.document，"
            "coverage_item_ids 只能填写被该 Evidence 事实直接或可迁移支持的输入 coverage_id，"
            "不能仅凭检索命中或关键词相同判定覆盖。\n"
            "评分统一使用 0 到 1：relevance=0 表示无关，0.25 表示仅关键词相关，0.5 表示"
            "可迁移但不直接，0.75 表示直接相关，1 表示对要求的完整直接支持；"
            "evidence_strength=0 表示无事实行动，0.4 表示有行动，0.7 表示有背景和行动，"
            "0.9 以上要求同时有具体结果；uniqueness=0 表示与其他候选完全重复，0.5 表示"
            "部分新增信息，1 表示提供不可替代的独特证据。\n"
            "unsupported_risk 只记录完成覆盖判断必须依赖、但 Evidence 中不存在的具体推断；"
            "无需推断时必须输出空数组。supported_skills 只能逐字复制该候选"
            "allowed_supported_skills 中的值，不得推断、概括或翻译；没有匹配项时输出空数组。",
            {
                "coverage_items": [
                    item.model_dump(mode="json") for item in analysis.coverage_items
                ],
                "tasks": [item.model_dump(mode="json") for item in tasks],
                "candidates": candidate_payloads,
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
            source_labels = {
                label.casefold(): label
                for label in _unique_labels(document.technologies + document.tags)
            }
            # 模型的相关性判断仍可使用完整 Evidence，但技能提升只能引用显式标签。
            # 丢弃越界值可以维持事实约束，且不让单个派生标签破坏整次生成。
            item.supported_skills = _unique_labels(
                [
                    source_labels[skill.casefold()]
                    for skill in item.supported_skills
                    if skill.casefold() in source_labels
                ]
            )
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
            "输入包含 analysis、plan、judgments、constraints、search_round 和"
            "has_new_candidates。评审当前计划是否有足够、直接、具体的事实证据支持目标岗位。"
            "gap_coverage_ids 必须列出仍缺少可靠证据的输入 coverage_id，不得引用未知 ID；"
            "不要只看 plan.coverage.covered，要综合 relevance、evidence_strength、"
            "unsupported_risk 和当前入选组合。\n"
            "严格按以下真值表决策：只有 gap_coverage_ids 非空、search_round 小于"
            "constraints.max_search_rounds，并且 search_round=1 或 has_new_candidates=true 时，"
            "才允许 actions 包含 search_more；此时 acceptable 必须为 false。其他所有情况"
            "actions 都不得包含 search_more，acceptable 必须为 true；仍有空缺时 actions 应"
            "包含 accept_with_gaps。warnings 只描述输入可证实的缺口或停止原因。",
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
            "输入包含 target_title、plan 和 selected_experiences。只使用 plan 中选中的"
            "Evidence 忠实生成简历草稿，并满足以下规则：\n"
            "1. experiences 必须与 plan.selected_experiences 的 experience_id 集合完全一致，"
            "每个已选经历恰好输出一次，不得新增、遗漏或改变 ID；\n"
            "2. 每个经历至少输出一个 bullet，数量不得超过对应 bullet_budget；\n"
            "3. bullet.experience_id 必须等于所属经历 ID；bullet.evidence_ids 只能引用该经历"
            "在 plan 中选中的 evidence_ids，并且 text 必须真实使用列出的每条 Evidence；\n"
            "4. text 只能压缩 Evidence 的 background、action、result，不得新增数字、指标、"
            "技能、职责、因果关系或结果；原文没有结果时不得补结果；\n"
            "5. summary 只能概括已选 Evidence 能直接支持的能力，不得照抄 JD 要求，不得新增"
            "未出现在已选 Evidence 中的数字、技能或候选人评价。",
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
