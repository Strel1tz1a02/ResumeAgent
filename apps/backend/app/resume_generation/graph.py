"""简历生成 LangGraph：显式保存计划、缺口与重规划轮次。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.workflow_runtime.events import RuntimeEvent
from app.resume_generation.model import ResumeGenerationModel
from app.resume_generation.observability import log_generation_trace
from app.resume_generation.planner import assemble_plan, materialize_resume
from app.resume_generation.retriever import (
    EvidenceRetriever,
    build_documents,
    merge_retrieval_rounds,
)
from app.resume_generation.schemas import (
    DraftFactCheckResult,
    EvidenceJudgment,
    ExperienceSnapshot,
    JDAnalysisSnapshot,
    JDAnalysisSourceSnapshot,
    ResumeConstraints,
    ResumeDraft,
    ResumePlan,
    ResumeProvenance,
    ResumeValidation,
    RetrievedEvidence,
    SearchTask,
)
from app.resume_generation.validation import (
    build_draft_validation_claims,
    compose_draft_validation,
    validate_draft_references,
)
from app.schemas.models import ResumeData


class ResumeGenerationState(TypedDict, total=False):
    run_id: str
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
    should_search_more: bool
    draft: ResumeDraft
    fact_check: DraftFactCheckResult
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
        traced_retrieve = getattr(
            dependencies.retriever,
            "retrieve_with_trace",
            None,
        )
        if traced_retrieve is None:
            current = await dependencies.retriever.retrieve(
                state["search_tasks"], documents
            )
        else:
            current = await traced_retrieve(
                state["search_tasks"],
                documents,
                run_id=state.get("run_id"),
                search_round=state.get("search_round"),
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
        fallback_events = getattr(dependencies.model, "fallback_events", [])
        fallback_count_before = list(fallback_events).count("judge")
        judgments = await dependencies.model.judge(
            state["analysis"],
            state["all_search_tasks"],
            state["retrieved"],
        )
        fallback_events = getattr(dependencies.model, "fallback_events", [])
        fallback_used = list(fallback_events).count("judge") > fallback_count_before
        candidates_by_id = {
            item.document.evidence_id: item for item in state["retrieved"]
        }
        judgment_rows: list[dict[str, Any]] = []
        for judgment in judgments:
            candidate = candidates_by_id.get(judgment.evidence_id)
            row = judgment.model_dump(mode="json")
            if candidate is not None:
                row.update(
                    {
                        "best_retrieval_score": candidate.retrieval_score,
                        "best_retrieval_score_scope": (
                            "max_across_tasks_and_completed_rounds"
                        ),
                        "retrieval_task_ids": candidate.task_ids,
                    }
                )
            judgment_rows.append(row)
        model_details = {
            "class": type(dependencies.model).__name__,
            "fallback_used": fallback_used,
        }
        primary_model = getattr(dependencies.model, "primary", None)
        fallback_model = getattr(dependencies.model, "fallback", None)
        if primary_model is not None:
            model_details["primary_class"] = type(primary_model).__name__
        if fallback_model is not None:
            model_details["fallback_class"] = type(fallback_model).__name__
        log_generation_trace(
            "resume_generation.evidence_scoring",
            run_id=state.get("run_id"),
            search_round=state.get("search_round"),
            payload={
                "status": "completed",
                "judge_model": model_details,
                "candidate_scope": "cumulative",
                "candidate_count": len(state["retrieved"]),
                "judgments": judgment_rows,
            },
        )
        return {"judgments": judgments}

    async def build_plan(state: ResumeGenerationState) -> dict[str, Any]:
        """拼装计划，并用确定性规则计算补搜目标与停止条件。"""
        plan = assemble_plan(
            state["analysis"],
            state["experiences"],
            state["judgments"],
            state["constraints"],
            search_rounds=state["search_round"],
        )
        importance_by_id = {
            item.coverage_id: item.importance
            for item in state["analysis"].coverage_items
        }
        gap_coverage_ids = [
            coverage_id
            for coverage_id in plan.uncovered_requirements
            if importance_by_id.get(coverage_id) in {"must", "should"}
        ]
        should_search_more = (
            bool(gap_coverage_ids)
            and state["search_round"] < state["constraints"].max_search_rounds
            and (
                state["search_round"] == 1
                or state.get("new_candidate_count", 0) > 0
            )
        )

        warnings: list[str] = []
        must_gaps = [
            coverage_id
            for coverage_id in gap_coverage_ids
            if importance_by_id[coverage_id] == "must"
        ]
        if must_gaps:
            warnings.append(f"仍有 {len(must_gaps)} 项必选要求没有事实证据")
        if plan.coverage_ratio < state["constraints"].min_coverage_ratio:
            warnings.append(
                f"加权覆盖率 {plan.coverage_ratio:.0%} 低于目标 "
                f"{state['constraints'].min_coverage_ratio:.0%}"
            )

        if should_search_more:
            actions = ["search_more"]
        else:
            actions = []
            if plan.promoted_skills:
                actions.append("move_to_skill")
            if plan.omitted_candidates:
                actions.append("drop_redundant_content")
            if plan.uncovered_requirements:
                actions.append("accept_with_gaps")
                warnings.append(
                    "已达到停止条件，未覆盖项将显式保留且不会生成虚构内容"
                )
        plan.review_actions = actions
        plan.review_warnings = warnings
        return {
            "plan": plan,
            "gap_coverage_ids": gap_coverage_ids,
            "should_search_more": should_search_more,
        }

    def route_after_plan(state: ResumeGenerationState) -> str:
        """根据规则决策进入下一轮检索或开始生成草稿。"""
        if state["should_search_more"]:
            return "plan_search"
        return "draft_resume"

    async def draft_resume(state: ResumeGenerationState) -> dict[str, Any]:
        draft = await dependencies.model.draft(
            state["analysis"], state["plan"], state["experiences"]
        )
        return {"draft": draft}

    async def validate_draft(state: ResumeGenerationState) -> dict[str, Any]:
        claims = build_draft_validation_claims(
            state["plan"], state["draft"], state["experiences"]
        )
        reference_checks = validate_draft_references(
            state["plan"], state["draft"], state["experiences"]
        )
        fact_check = await dependencies.model.validate_draft(
            state["draft"], state["plan"], state["experiences"]
        )
        fallback_events = list(
            dict.fromkeys(getattr(dependencies.model, "fallback_events", []))
        )
        validation = compose_draft_validation(
            plan=state["plan"],
            claims=claims,
            reference_checks=reference_checks,
            fact_check=fact_check,
            fallback_events=fallback_events,
        )
        log_generation_trace(
            "resume_generation.draft_validation",
            run_id=state.get("run_id"),
            search_round=state.get("search_round"),
            payload={
                "status": "completed",
                "valid": validation.valid,
                "model_validation_status": validation.model_validation_status,
                "checks": [
                    {
                        "source": check.source,
                        "status": check.status,
                        "claim_id": check.claim_id,
                        "kind": check.kind,
                        "experience_id": check.experience_id,
                        "bullet_index": check.bullet_index,
                        "evidence_ids": check.evidence_ids,
                        "verdict": check.verdict,
                    }
                    for check in validation.checks
                ],
            },
        )
        return {"fact_check": fact_check, "validation": validation}

    async def materialize_draft(state: ResumeGenerationState) -> dict[str, Any]:
        resume_data, provenance = materialize_resume(
            state["analysis"],
            state["plan"],
            state["draft"],
            state["experiences"],
        )
        result = {
            "analysis": state["analysis"].model_dump(mode="json"),
            "plan": state["plan"].model_dump(mode="json"),
            "resume_data": resume_data.model_dump(mode="json"),
            "provenance": provenance.model_dump(mode="json"),
            "validation": state["validation"].model_dump(mode="json"),
        }
        get_stream_writer()(
            RuntimeEvent(
                "result.available",
                {"kind": "resume_generation", "result": result},
            )
        )
        return {"resume_data": resume_data, "provenance": provenance}

    graph = StateGraph(ResumeGenerationState)
    graph.add_node("analyze_jd", analyze_jd)
    graph.add_node("plan_search", plan_search)
    graph.add_node("retrieve", retrieve)
    graph.add_node("judge_evidence", judge_evidence)
    graph.add_node("assemble_plan", build_plan)
    graph.add_node("draft_resume", draft_resume)
    graph.add_node("validate_draft", validate_draft)
    graph.add_node("materialize_resume", materialize_draft)
    graph.add_edge(START, "analyze_jd")
    graph.add_edge("analyze_jd", "plan_search")
    graph.add_edge("plan_search", "retrieve")
    graph.add_edge("retrieve", "judge_evidence")
    graph.add_edge("judge_evidence", "assemble_plan")
    graph.add_conditional_edges(
        "assemble_plan",
        route_after_plan,
        {"plan_search": "plan_search", "draft_resume": "draft_resume"},
    )
    graph.add_edge("draft_resume", "validate_draft")
    graph.add_edge("validate_draft", "materialize_resume")
    graph.add_edge("materialize_resume", END)
    return graph
