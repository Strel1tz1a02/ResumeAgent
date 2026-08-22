"""简历生成 LangGraph：显式保存计划、缺口与重规划轮次。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.config import get_stream_writer

from app.ai_chat.streaming.events import RuntimeEvent
from app.resume_generation.model import ResumeGenerationModel
from app.resume_generation.planner import (
    assemble_plan,
    materialize_resume,
    validate_generation,
)
from app.resume_generation.retriever import (
    EvidenceRetriever,
    build_documents,
    merge_retrieval_rounds,
)
from app.resume_generation.schemas import (
    EvidenceJudgment,
    ExperienceSnapshot,
    JDAnalysisSnapshot,
    JDAnalysisSourceSnapshot,
    PlanCritique,
    ResumeConstraints,
    ResumeDraft,
    ResumePlan,
    ResumeProvenance,
    ResumeValidation,
    RetrievedEvidence,
    SearchTask,
)
from app.schemas.models import ResumeData


class ResumeGenerationState(TypedDict, total=False):
    jd_source: JDAnalysisSourceSnapshot
    experiences: list[ExperienceSnapshot]
    constraints: ResumeConstraints
    analysis: JDAnalysisSnapshot
    search_round: int
    gap_coverage_ids: list[str]
    search_tasks: list[SearchTask]
    all_search_tasks: list[SearchTask]
    retrieved: list[RetrievedEvidence]
    new_candidate_count: int
    judgments: list[EvidenceJudgment]
    plan: ResumePlan
    critique: PlanCritique
    draft: ResumeDraft
    resume_data: ResumeData
    provenance: ResumeProvenance
    validation: ResumeValidation


@dataclass(frozen=True)
class ResumeGenerationGraphDependencies:
    model: ResumeGenerationModel
    retriever: EvidenceRetriever


def build_resume_generation_graph(
    dependencies: ResumeGenerationGraphDependencies,
) -> StateGraph:
    async def analyze_jd(state: ResumeGenerationState) -> dict[str, Any]:
        coverage_items = await dependencies.model.analyze_jd(state["jd_source"])
        return {
            "analysis": JDAnalysisSnapshot(
                source=state["jd_source"],
                target_title=state["jd_source"].job_name,
                coverage_items=coverage_items,
            ),
            "search_round": 0,
            "gap_coverage_ids": [],
            "all_search_tasks": [],
            "retrieved": [],
        }

    async def plan_search(state: ResumeGenerationState) -> dict[str, Any]:
        search_round = state.get("search_round", 0) + 1
        tasks = await dependencies.model.plan_search(
            state["analysis"],
            gap_coverage_ids=state.get("gap_coverage_ids", []),
            search_round=search_round,
            top_k=state["constraints"].top_k_per_task,
        )
        existing = {item.task_id: item for item in state.get("all_search_tasks", [])}
        existing.update({item.task_id: item for item in tasks})
        return {
            "search_round": search_round,
            "search_tasks": tasks,
            "all_search_tasks": list(existing.values()),
        }

    async def retrieve(state: ResumeGenerationState) -> dict[str, Any]:
        documents = build_documents(state["experiences"])
        current = await dependencies.retriever.retrieve(
            state["search_tasks"], documents
        )
        previous = state.get("retrieved", [])
        previous_ids = {item.document.evidence_id for item in previous}
        merged = merge_retrieval_rounds(previous, current)
        return {
            "retrieved": merged,
            "new_candidate_count": len(
                {item.document.evidence_id for item in merged} - previous_ids
            ),
        }

    async def judge_evidence(state: ResumeGenerationState) -> dict[str, Any]:
        judgments = await dependencies.model.judge(
            state["analysis"],
            state["all_search_tasks"],
            state["retrieved"],
        )
        return {"judgments": judgments}

    async def build_plan(state: ResumeGenerationState) -> dict[str, Any]:
        plan = assemble_plan(
            state["analysis"],
            state["experiences"],
            state["judgments"],
            state["constraints"],
            search_rounds=state["search_round"],
        )
        return {"plan": plan}

    async def critique(state: ResumeGenerationState) -> dict[str, Any]:
        result = await dependencies.model.critique(
            state["analysis"],
            state["plan"],
            state["judgments"],
            state["constraints"],
            search_round=state["search_round"],
            has_new_candidates=state.get("new_candidate_count", 0) > 0,
        )
        plan = state["plan"].model_copy(deep=True)
        # “空缺”是模型基于证据质量作出的语义结论，不由服务端覆盖率规则代判。
        plan.uncovered_requirements = result.gap_coverage_ids
        plan.review_actions = list(dict.fromkeys(plan.review_actions + result.actions))
        plan.review_warnings = list(
            dict.fromkeys(plan.review_warnings + result.warnings)
        )
        return {
            "critique": result,
            "plan": plan,
            "gap_coverage_ids": result.gap_coverage_ids,
        }

    def route_after_critique(state: ResumeGenerationState) -> str:
        if "search_more" in state["critique"].actions:
            return "plan_search"
        return "draft_resume"

    async def draft_resume(state: ResumeGenerationState) -> dict[str, Any]:
        draft = await dependencies.model.draft(
            state["analysis"], state["plan"], state["experiences"]
        )
        resume_data, provenance = materialize_resume(
            state["analysis"],
            state["plan"],
            draft,
            state["experiences"],
        )
        return {
            "draft": draft,
            "resume_data": resume_data,
            "provenance": provenance,
        }

    async def verify_resume(state: ResumeGenerationState) -> dict[str, Any]:
        validation = validate_generation(
            state["plan"],
            state["resume_data"],
            state["provenance"],
            state["experiences"],
            state["constraints"],
        )
        fallback_events = list(
            dict.fromkeys(getattr(dependencies.model, "fallback_events", []))
        )
        if fallback_events:
            validation.warnings.append(
                "auto 模式在以下阶段使用了确定性降级: " + ", ".join(fallback_events)
            )
        result = {
            "analysis": state["analysis"].model_dump(mode="json"),
            "plan": state["plan"].model_dump(mode="json"),
            "resume_data": state["resume_data"].model_dump(mode="json"),
            "provenance": state["provenance"].model_dump(mode="json"),
            "validation": validation.model_dump(mode="json"),
        }
        get_stream_writer()(
            RuntimeEvent(
                "result.available",
                {"kind": "resume_generation", "result": result},
            )
        )
        return {"validation": validation}

    graph = StateGraph(ResumeGenerationState)
    graph.add_node("analyze_jd", analyze_jd)
    graph.add_node("plan_search", plan_search)
    graph.add_node("retrieve", retrieve)
    graph.add_node("judge_evidence", judge_evidence)
    graph.add_node("assemble_plan", build_plan)
    graph.add_node("critique_plan", critique)
    graph.add_node("draft_resume", draft_resume)
    graph.add_node("verify_resume", verify_resume)
    graph.add_edge(START, "analyze_jd")
    graph.add_edge("analyze_jd", "plan_search")
    graph.add_edge("plan_search", "retrieve")
    graph.add_edge("retrieve", "judge_evidence")
    graph.add_edge("judge_evidence", "assemble_plan")
    graph.add_edge("assemble_plan", "critique_plan")
    graph.add_conditional_edges(
        "critique_plan",
        route_after_critique,
        {"plan_search": "plan_search", "draft_resume": "draft_resume"},
    )
    graph.add_edge("draft_resume", "verify_resume")
    graph.add_edge("verify_resume", END)
    return graph
