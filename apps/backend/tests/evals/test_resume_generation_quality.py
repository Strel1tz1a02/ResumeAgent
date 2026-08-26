"""简历生成评分器、固定合成参考答案与生产 Graph 文案质量评测。"""

import copy
from collections.abc import Iterator
from dataclasses import asdict
from typing import Any

import pytest

from app import llm as llm_module
from app.resume_generation.graph import (
    ResumeGenerationGraphDependencies,
    build_resume_generation_graph,
)
from app.resume_generation.model import (
    FallbackResumeGenerationModel,
    LangChainResumeGenerationModel,
    RuleBasedResumeGenerationModel,
)
from app.resume_generation.retriever import build_documents, merge_retrieval_rounds
from app.resume_generation.schemas import (
    ExperienceSnapshot,
    JDAnalysisSourceSnapshot,
    ResumeConstraints,
    ResumeProvenance,
    RetrievedEvidence,
    SearchTask,
)
from app.schemas.models import ResumeData
from tests.evals.golden.resume_generation_cases import RESUME_GENERATION_CASES
from tests.evals.quality_eval_support import (
    get_eval_judge_config,
    judge_model_relation,
    judge_outputs,
    model_metadata,
    require_llm,
)
from tests.evals.quality_report import write_quality_report
from tests.evals.quality_scorers import (
    GenerationReferenceQuality,
    score_generation,
    score_generation_reference,
)

_EXECUTION_CHECK_NAMES = frozenset(
    {"validation_valid", "model_validation_status", "no_fallback"}
)
_TRUTHFULNESS_CHECK_NAMES = frozenset(
    {
        "grounded_number_precision",
        "no_forbidden_facts",
        "judge_no_unsupported_claims",
        "reference_provenance_grounded_number_precision",
        "reference_no_ungrounded_numbers",
        "reference_no_unknown_evidence_ids",
        "reference_no_cross_experience_evidence",
        "reference_all_bullets_have_provenance",
        "reference_all_skills_have_provenance",
        "reference_all_skills_grounded_to_provenance",
        "reference_no_ghost_provenance",
    }
)
_SELECTION_CHECK_NAMES = frozenset(
    {
        "reference_experience_precision",
        "reference_experience_recall",
        "reference_experience_f1",
        "reference_evidence_precision",
        "reference_evidence_recall",
        "reference_evidence_f1",
    }
)
_SUITE_MACRO_METRICS = (
    "requirement_coverage",
    "experience_f1",
    "evidence_f1",
    "technical_skill_f1",
    "summary_fact_recall",
    "required_section_recall",
    "core_fact_recall",
    "judge_overall",
)
_MODEL_CALL_CONTRACT = {
    "generation_model_stages_per_case": 5,
    "semantic_judge_stages_per_case": 1,
    "minimum_provider_invocations_per_case": 6,
    "maximum_application_level_invocations_per_case": 23,
    "minimum_provider_invocations_per_suite": 30,
    "maximum_application_level_invocations_per_suite": 115,
    "transport_retries_per_invocation": 0,
    "actual_usage_instrumented": False,
}


def _good_generated_resume() -> dict:
    return {
        "summary": "具备 Python/FastAPI、Redis 性能优化与可观测性实践。",
        "workExperience": [
            {
                "id": 1,
                "title": "后端工程师",
                "company": "星河科技",
                "years": "2024-03 - 2025-01",
                "description": [
                    "使用 FastAPI 异步接口与 Redis 缓存重构支付链路，P99 延迟从 420ms 降至 180ms，吞吐量提升 35%",
                    "建设链路追踪与分级告警，将 MTTR 从 50 分钟降至 18 分钟",
                ],
            }
        ],
        "personalProjects": [],
        "additional": {"technicalSkills": ["Python", "FastAPI", "Redis"]},
    }


def _score_against_reference(
    case: dict[str, Any],
    resume: dict[str, Any],
    provenance: dict[str, Any],
) -> GenerationReferenceQuality:
    """用案例自己的固定 Oracle 计算参考答案对齐指标。"""
    oracle = case["oracle"]
    return score_generation_reference(
        resume,
        provenance,
        case["experiences"],
        case["reference_resume"],
        case["reference_provenance"],
        expected_experience_ids=oracle["expected_experience_ids"],
        expected_evidence_ids=oracle["expected_evidence_ids"],
        core_fact_groups=oracle["core_fact_groups"],
        summary_fact_groups=oracle["summary_fact_groups"],
    )


def _reference_threshold_checks(
    score: GenerationReferenceQuality,
    thresholds: dict[str, Any],
    *,
    check_core_facts: bool = True,
) -> dict[str, bool]:
    """返回固定参考答案门槛的逐项、可写入报告结果。"""
    checks = {
        "experience_precision": (
            score.experience_precision >= thresholds["minimum_experience_precision"]
        ),
        "experience_recall": (
            score.experience_recall >= thresholds["minimum_experience_recall"]
        ),
        "experience_f1": score.experience_f1 >= thresholds["minimum_experience_f1"],
        "evidence_precision": (
            score.evidence_precision >= thresholds["minimum_evidence_precision"]
        ),
        "evidence_recall": (
            score.evidence_recall >= thresholds["minimum_evidence_recall"]
        ),
        "evidence_f1": score.evidence_f1 >= thresholds["minimum_evidence_f1"],
        "technical_skill_precision": (
            score.technical_skill_precision
            >= thresholds["minimum_technical_skill_precision"]
        ),
        "technical_skill_recall": (
            score.technical_skill_recall >= thresholds["minimum_technical_skill_recall"]
        ),
        "technical_skill_f1": (
            score.technical_skill_f1 >= thresholds["minimum_technical_skill_f1"]
        ),
        "summary_fact_recall": (
            score.summary_fact_recall >= thresholds["minimum_summary_fact_recall"]
        ),
        "required_section_recall": (
            score.required_section_recall
            >= thresholds["minimum_required_section_recall"]
        ),
        "provenance_grounded_number_precision": (
            score.provenance_grounded_number_precision
            >= thresholds["minimum_provenance_grounded_number_precision"]
        ),
        "no_ungrounded_numbers": not score.ungrounded_numbers,
        "no_unknown_evidence_ids": not score.unknown_evidence_ids,
        "no_cross_experience_evidence": not score.cross_experience_evidence,
        "all_bullets_have_provenance": not score.missing_bullet_provenance,
        "all_skills_have_provenance": not score.missing_skill_provenance,
        "all_skills_grounded_to_provenance": (not score.ungrounded_skill_provenance),
        "no_ghost_provenance": not score.ghost_provenance,
    }
    if check_core_facts:
        checks["core_fact_recall"] = (
            score.core_fact_recall >= thresholds["minimum_core_fact_recall"]
        )
    return checks


def _quality_dimensions(checks: dict[str, bool]) -> dict[str, bool]:
    """把失败区分为运行、真实性、选择和成稿，不让空输出冒充幻觉。"""

    def all_pass(names: set[str] | frozenset[str]) -> bool:
        return all(checks.get(name, False) for name in names)

    execution_passed = all_pass(_EXECUTION_CHECK_NAMES)
    truthfulness_passed = all_pass(_TRUTHFULNESS_CHECK_NAMES)
    selection_passed = all_pass(_SELECTION_CHECK_NAMES)
    composition_names = (
        set(checks)
        - _EXECUTION_CHECK_NAMES
        - _TRUTHFULNESS_CHECK_NAMES
        - _SELECTION_CHECK_NAMES
    )
    composition_passed = all_pass(composition_names)
    return {
        "execution_passed": execution_passed,
        "truthfulness_passed": truthfulness_passed,
        "safety_passed": execution_passed and truthfulness_passed,
        "selection_passed": selection_passed,
        "composition_passed": composition_passed,
        "content_passed": selection_passed and composition_passed,
    }


def _has_resume_content(resume: dict[str, Any]) -> bool:
    return bool(
        str(resume.get("summary", "")).strip()
        or resume.get("workExperience")
        or resume.get("personalProjects")
        or resume.get("additional", {}).get("technicalSkills")
    )


def _aggregate_generation_suite(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """聚合整套案例；不允许单案例高分冒充套件通过。"""
    expected_names = [case["name"] for case in RESUME_GENERATION_CASES]
    evaluated_names = [record["case"]["name"] for record in records]
    if len(evaluated_names) != len(set(evaluated_names)):
        raise ValueError("简历生成套件包含重复案例")

    expected_count = len(expected_names)
    evaluated_count = len(records)
    passed_count = sum(bool(record["summary"].get("passed")) for record in records)
    suite_complete = set(evaluated_names) == set(expected_names)
    independent_judge_count = sum(
        record["summary"].get("judge_is_independent") is True for record in records
    )
    reference_reviews = [
        record["case"].get("reference_review", {}) for record in records
    ]
    expert_reviewed_count = sum(
        review.get("status") == "expert_reviewed"
        and review.get("expert_reviewed") is True
        for review in reference_reviews
    )
    blind_reviewed_count = sum(
        review.get("blind_reviewed") is True for review in reference_reviews
    )
    reference_status_counts: dict[str, int] = {}
    for review in reference_reviews:
        status = str(review.get("status", "missing"))
        reference_status_counts[status] = reference_status_counts.get(status, 0) + 1

    dimension_names = (
        "execution_passed",
        "truthfulness_passed",
        "safety_passed",
        "selection_passed",
        "composition_passed",
        "content_passed",
    )
    dimensions_by_case: dict[str, dict[str, bool | None]] = {}
    for record in records:
        summary = record["summary"]
        checks = summary.get("checks")
        if isinstance(checks, dict):
            dimensions = _quality_dimensions(checks)
        else:
            dimensions = {
                name: summary.get(name) if isinstance(summary.get(name), bool) else None
                for name in dimension_names
            }
        dimensions_by_case[record["case"]["name"]] = dimensions

    dimension_values = {
        name: [
            dimensions[name]
            for dimensions in dimensions_by_case.values()
            if isinstance(dimensions[name], bool)
        ]
        for name in dimension_names
    }
    dimension_pass_rates = {
        name: sum(values) / len(values) if values else None
        for name, values in dimension_values.items()
    }
    dimension_scored_case_counts = {
        name: len(values) for name, values in dimension_values.items()
    }

    exact_selection_cases: list[str] = []
    hard_negative_selection_cases: list[str] = []
    empty_output_cases: list[str] = []
    for record in records:
        case = record["case"]
        selected_ids = {
            item["experience_id"]
            for item in case.get("plan", {}).get("selected_experiences", [])
        }
        oracle = case.get("input", {}).get("oracle", {})
        expected_ids = set(oracle.get("expected_experience_ids", []))
        hard_negative_ids = set(oracle.get("hard_negative_experience_ids", []))
        if selected_ids == expected_ids:
            exact_selection_cases.append(case["name"])
        if selected_ids & hard_negative_ids:
            hard_negative_selection_cases.append(case["name"])
        if "resume" in case and not _has_resume_content(case["resume"]):
            empty_output_cases.append(case["name"])

    macro_metrics: dict[str, float] = {}
    for name in _SUITE_MACRO_METRICS:
        values = [
            float(record["summary"][name])
            for record in records
            if isinstance(record["summary"].get(name), int | float)
            and not isinstance(record["summary"].get(name), bool)
        ]
        macro_metrics[name] = sum(values) / len(values) if values else 0.0

    failed_checks = {
        record["case"]["name"]: [
            name
            for name, passed in record["summary"].get("checks", {}).items()
            if not passed
        ]
        for record in records
        if not record["summary"].get("passed")
    }
    regression_passed = suite_complete and passed_count == expected_count
    benchmark_qualified = suite_complete and expert_reviewed_count == expected_count
    return {
        "passed": regression_passed,
        "regression_passed": regression_passed,
        "suite_complete": suite_complete,
        "expected_case_count": expected_count,
        "evaluated_case_count": evaluated_count,
        "passed_case_count": passed_count,
        "pass_rate": passed_count / expected_count if expected_count else 0.0,
        "evaluated_pass_rate": (
            passed_count / evaluated_count if evaluated_count else 0.0
        ),
        "independent_judge_case_count": independent_judge_count,
        "independent_judge_coverage": (
            independent_judge_count / evaluated_count if evaluated_count else 0.0
        ),
        "reference_status_counts": reference_status_counts,
        "expert_reviewed_case_count": expert_reviewed_count,
        "expert_review_coverage": (
            expert_reviewed_count / evaluated_count if evaluated_count else 0.0
        ),
        "blind_review_coverage": (
            blind_reviewed_count / evaluated_count if evaluated_count else 0.0
        ),
        "benchmark_qualified": benchmark_qualified,
        "quality_claim_scope": (
            "expert_reviewed_benchmark"
            if benchmark_qualified
            else "synthetic_regression_only"
        ),
        "quality_claim_ready": (
            regression_passed
            and benchmark_qualified
            and independent_judge_count == expected_count
        ),
        "dimension_pass_rates": dimension_pass_rates,
        "dimension_scored_case_counts": dimension_scored_case_counts,
        "exact_experience_selection_rate": (
            len(exact_selection_cases) / evaluated_count if evaluated_count else 0.0
        ),
        "exact_experience_selection_cases": exact_selection_cases,
        "hard_negative_selection_rate": (
            len(hard_negative_selection_cases) / evaluated_count
            if evaluated_count
            else 0.0
        ),
        "hard_negative_selection_cases": hard_negative_selection_cases,
        "empty_output_rate": (
            len(empty_output_cases) / evaluated_count if evaluated_count else 0.0
        ),
        "empty_output_cases": empty_output_cases,
        "macro_metrics": macro_metrics,
        "failed_checks": failed_checks,
        "evaluated_case_names": evaluated_names,
        "missing_case_names": sorted(set(expected_names) - set(evaluated_names)),
    }


@pytest.fixture(scope="module")
def generation_suite_records() -> Iterator[list[dict[str, Any]]]:
    """真实案例即使逐个失败，也在模块结束时落一份完整套件报告。"""
    records: list[dict[str, Any]] = []
    yield records
    if not records:
        return

    first = records[0]
    suite_summary = _aggregate_generation_suite(records)
    path = write_quality_report(
        "resume-generation-suite",
        model=first["model"],
        thresholds={record["case"]["name"]: record["thresholds"] for record in records},
        cases=[
            {**record["case"], "case_summary": record["summary"]} for record in records
        ],
        summary=suite_summary,
        report_version="2",
    )
    print(f"\n简历生成套件报告：{path}")


def _record_generation_suite_result(
    records: list[dict[str, Any]],
    *,
    case: dict[str, Any],
    summary: dict[str, Any],
    model: dict[str, Any],
    thresholds: dict[str, Any],
) -> None:
    records.append(
        {
            "case": case,
            "summary": summary,
            "model": model,
            "thresholds": thresholds,
        }
    )


def _failed_generation_summary(
    *,
    stage: str,
    error: str,
    fallback_events: list[str],
    fallback_errors: list[dict[str, str]],
) -> dict[str, Any]:
    """运行中断时把未评分维度保留为未知，而不是误报为质量失败。"""
    return {
        "passed": False,
        "execution_passed": False,
        "truthfulness_passed": None,
        "safety_passed": False,
        "selection_passed": None,
        "composition_passed": None,
        "content_passed": None,
        "failure_stage": stage,
        "error": error,
        "fallback_events": fallback_events,
        "fallback_errors": fallback_errors,
    }


def test_failed_generation_summary_preserves_fallback_diagnostics() -> None:
    fallback_errors = [
        {
            "stage": "analyze_jd",
            "primary_model": "LangChainResumeGenerationModel",
            "fallback_model": "RuleBasedResumeGenerationModel",
            "exception_type": "ValueError",
            "message": "JD analysis omitted one or more source requirements",
        }
    ]

    summary = _failed_generation_summary(
        stage="fallback",
        error="生成阶段触发 fallback；未调用 Judge",
        fallback_events=["analyze_jd"],
        fallback_errors=fallback_errors,
    )

    assert summary["fallback_errors"] == fallback_errors


def _disable_quality_eval_provider_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保留结构修复重试，但关闭 SDK 传输重试以限制隐藏调用成本。"""
    original_get_model = llm_module.get_chat_model

    def get_model_without_transport_retries(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Any, Any]:
        kwargs["max_retries"] = 0
        return original_get_model(*args, **kwargs)

    monkeypatch.setattr(
        llm_module,
        "get_chat_model",
        get_model_without_transport_retries,
    )


def _pipeline_diagnostics(state: dict[str, Any]) -> dict[str, Any]:
    """保留中间产物，便于区分分析、筛选、规划和成稿阶段的失败。"""
    return {
        "analysis": state["analysis"].model_dump(mode="json"),
        "search_tasks": [
            item.model_dump(mode="json") for item in state.get("all_search_tasks", [])
        ],
        "retrieved": [
            item.model_dump(mode="json") for item in state.get("retrieved", [])
        ],
        "judgments": [
            item.model_dump(mode="json") for item in state.get("judgments", [])
        ],
        "draft": state["draft"].model_dump(mode="json"),
        "fact_check": state["fact_check"].model_dump(mode="json"),
    }


def test_quality_dimensions_do_not_call_empty_output_unsafe() -> None:
    checks = {name: True for name in _EXECUTION_CHECK_NAMES | _TRUTHFULNESS_CHECK_NAMES}
    checks.update({name: False for name in _SELECTION_CHECK_NAMES})
    checks.update(
        {
            "has_resume_content": False,
            "minimum_bullets": False,
            "judge_grounding": False,
            "judge_overall": False,
        }
    )

    dimensions = _quality_dimensions(checks)

    assert dimensions["execution_passed"]
    assert dimensions["truthfulness_passed"]
    assert dimensions["safety_passed"]
    assert not dimensions["selection_passed"]
    assert not dimensions["composition_passed"]
    assert not dimensions["content_passed"]


def test_quality_eval_disables_hidden_transport_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[dict[str, Any]] = []

    def fake_get_model(*_args: Any, **kwargs: Any) -> tuple[object, object]:
        received.append(kwargs)
        return object(), object()

    monkeypatch.setattr(llm_module, "get_chat_model", fake_get_model)
    _disable_quality_eval_provider_retries(monkeypatch)
    llm_module.get_chat_model(max_retries=99)

    assert received == [{"max_retries": 0}]


def test_generation_suite_aggregation_exposes_systemic_failures() -> None:
    records: list[dict[str, Any]] = []
    for index, fixture in enumerate(RESUME_GENERATION_CASES):
        oracle = fixture["oracle"]
        if index == 0:
            selected_ids = oracle["expected_experience_ids"]
        elif index < 3:
            selected_ids = oracle["hard_negative_experience_ids"]
        else:
            selected_ids = []
        has_content = index < 3
        checks = {
            name: True for name in _EXECUTION_CHECK_NAMES | _TRUTHFULNESS_CHECK_NAMES
        }
        checks.update({name: index == 0 for name in _SELECTION_CHECK_NAMES})
        checks.update(
            {
                "has_resume_content": has_content,
                "reference_technical_skill_f1": False,
                "judge_overall": False,
            }
        )
        records.append(
            {
                "case": {
                    "name": fixture["name"],
                    "reference_review": fixture["reference_review"],
                    "input": {"oracle": oracle},
                    "plan": {
                        "selected_experiences": [
                            {"experience_id": experience_id}
                            for experience_id in selected_ids
                        ]
                    },
                    "resume": {"summary": "有内容"} if has_content else {},
                },
                "summary": {
                    "passed": False,
                    "judge_is_independent": index == 0,
                    "checks": checks,
                    **{
                        name: 1.0 if index == 0 else 0.0
                        for name in _SUITE_MACRO_METRICS
                    },
                },
            }
        )

    summary = _aggregate_generation_suite(records)

    assert summary["suite_complete"]
    assert not summary["passed"]
    assert summary["pass_rate"] == 0.0
    assert summary["dimension_pass_rates"]["truthfulness_passed"] == 1.0
    assert summary["dimension_pass_rates"]["selection_passed"] == 0.2
    assert summary["dimension_pass_rates"]["content_passed"] == 0.0
    assert summary["independent_judge_coverage"] == 0.2
    assert summary["expert_review_coverage"] == 0.0
    assert summary["reference_status_counts"] == {
        "synthetic_fixture_not_expert_reviewed": 5
    }
    assert not summary["benchmark_qualified"]
    assert summary["quality_claim_scope"] == "synthetic_regression_only"
    assert not summary["quality_claim_ready"]
    assert summary["exact_experience_selection_rate"] == 0.2
    assert summary["hard_negative_selection_rate"] == 0.4
    assert summary["empty_output_rate"] == 0.4

    qualified_records = copy.deepcopy(records)
    for record in qualified_records:
        record["case"]["reference_review"] = {
            "status": "expert_reviewed",
            "expert_reviewed": True,
            "blind_reviewed": True,
            "reviewer_role": "recruiting_domain_expert",
            "review_method": "blind_reference_review",
            "reviewed_at": "2026-08-25T00:00:00Z",
        }
        record["summary"]["passed"] = True
        record["summary"]["judge_is_independent"] = True
        record["summary"]["checks"] = {
            name: True for name in record["summary"]["checks"]
        }

    qualified = _aggregate_generation_suite(qualified_records)

    assert qualified["regression_passed"]
    assert qualified["benchmark_qualified"]
    assert qualified["quality_claim_scope"] == "expert_reviewed_benchmark"
    assert qualified["quality_claim_ready"]


def test_generation_scorer_accepts_grounded_relevant_resume() -> None:
    case = RESUME_GENERATION_CASES[0]
    score = score_generation(
        _good_generated_resume(),
        case["experiences"],
        requirement_groups=case["requirement_groups"],
        forbidden_fragments=case["forbidden_fragments"],
    )

    assert score.requirement_coverage == 0.75
    assert score.grounded_number_precision == 1.0
    assert score.bullet_count == 2
    assert score.invented_numbers == ()
    assert score.forbidden_hits == ()


def test_generation_scorer_finds_unsupported_content() -> None:
    case = RESUME_GENERATION_CASES[0]
    bad = _good_generated_resume()
    bad["summary"] = "字节跳动 Kubernetes 架构师，营收翻倍。"
    bad["workExperience"][0]["description"] = ["性能提升 99%"]
    score = score_generation(
        bad,
        case["experiences"],
        requirement_groups=case["requirement_groups"],
        forbidden_fragments=case["forbidden_fragments"],
    )

    # 关键词覆盖会被虚构内容“刷高”，必须与 grounding/forbidden 联合判定。
    assert score.requirement_coverage == 0.75
    assert "99%" in score.invented_numbers
    assert score.grounded_number_precision == 0.0
    assert {"字节跳动", "营收翻倍", "Kubernetes"} <= set(score.forbidden_hits)


@pytest.mark.parametrize("case", RESUME_GENERATION_CASES, ids=lambda item: item["name"])
def test_generation_reference_is_valid_and_self_consistent(
    case: dict[str, Any],
) -> None:
    """标准简历必须满足生产 Schema，并完整表达其声明的核心事实。"""
    ResumeData.model_validate(case["reference_resume"])
    ResumeProvenance.model_validate(case["reference_provenance"])
    JDAnalysisSourceSnapshot.model_validate(case["jd_source"])
    for item in case["experiences"]:
        ExperienceSnapshot.model_validate(item)
    ResumeConstraints.model_validate(case["constraints"])

    reference_score = _score_against_reference(
        case,
        case["reference_resume"],
        case["reference_provenance"],
    )
    assert reference_score.experience_precision == 1.0
    assert reference_score.experience_recall == 1.0
    assert reference_score.experience_f1 == 1.0
    assert reference_score.evidence_precision == 1.0
    assert reference_score.evidence_recall == 1.0
    assert reference_score.evidence_f1 == 1.0
    assert reference_score.technical_skill_precision == 1.0
    assert reference_score.technical_skill_recall == 1.0
    assert reference_score.technical_skill_f1 == 1.0
    assert reference_score.summary_fact_recall == 1.0
    assert reference_score.required_section_recall == 1.0
    assert reference_score.missing_technical_skills == ()
    assert reference_score.unexpected_technical_skills == ()
    assert reference_score.missing_summary_fact_groups == ()
    assert reference_score.missing_required_sections == ()
    assert reference_score.core_fact_recall == 1.0
    assert reference_score.missing_core_fact_groups == ()
    assert reference_score.provenance_grounded_number_precision == 1.0
    assert reference_score.ungrounded_numbers == ()
    assert reference_score.unknown_evidence_ids == ()
    assert reference_score.cross_experience_evidence == ()
    assert reference_score.missing_bullet_provenance == ()
    assert reference_score.missing_skill_provenance == ()
    assert reference_score.ungrounded_skill_provenance == ()
    assert reference_score.ghost_provenance == ()


def test_generation_reference_scorer_penalizes_missing_selection_and_facts() -> None:
    case = RESUME_GENERATION_CASES[0]
    resume = copy.deepcopy(case["reference_resume"])
    provenance = copy.deepcopy(case["reference_provenance"])
    resume["summary"] = "具备 Python、FastAPI 与 Redis 后端开发经验。"
    resume["workExperience"] = resume["workExperience"][:1]
    resume["additional"]["technicalSkills"] = ["Python", "FastAPI", "Redis"]
    provenance["summary_evidence_ids"] = [101]
    provenance["bullets"] = provenance["bullets"][:1]
    provenance["skills"] = provenance["skills"][:3]

    score = _score_against_reference(case, resume, provenance)

    assert score.experience_precision == 1.0
    assert score.experience_recall == 0.5
    assert score.experience_f1 == pytest.approx(2 / 3)
    assert score.evidence_precision == 1.0
    assert score.evidence_recall == 0.5
    assert score.evidence_f1 == pytest.approx(2 / 3)
    assert score.core_fact_recall < 1.0
    assert score.missing_core_fact_groups


def test_generation_reference_scorer_rejects_empty_skills_and_weak_summary() -> None:
    """复现真实坏样本：经历正确也不能掩盖技能栏和摘要缺失。"""
    case = RESUME_GENERATION_CASES[0]
    resume = copy.deepcopy(case["reference_resume"])
    provenance = copy.deepcopy(case["reference_provenance"])
    resume["summary"] = "具备后端性能优化与可观测性治理经验。"
    resume["additional"]["technicalSkills"] = []
    provenance["skills"] = []

    score = _score_against_reference(case, resume, provenance)

    assert score.experience_f1 == 1.0
    assert score.evidence_f1 == 1.0
    assert score.technical_skill_recall == 0.0
    assert score.technical_skill_f1 == 0.0
    assert score.summary_fact_recall < 0.5
    assert score.required_section_recall < 1.0
    assert set(score.missing_technical_skills) == {
        "Python",
        "FastAPI",
        "Redis",
        "Prometheus",
        "Grafana",
        "OpenTelemetry",
    }
    failed = [
        name
        for name, passed in _reference_threshold_checks(
            score, case["thresholds"]
        ).items()
        if not passed
    ]
    assert {
        "technical_skill_recall",
        "technical_skill_f1",
        "summary_fact_recall",
        "required_section_recall",
    } <= set(failed)


@pytest.mark.parametrize("case", RESUME_GENERATION_CASES, ids=lambda item: item["name"])
def test_each_reference_case_rejects_missing_summary_and_skills(
    case: dict[str, Any],
) -> None:
    """每个金标都必须对内容缺失敏感，而不是只有首个案例能检出。"""
    resume = copy.deepcopy(case["reference_resume"])
    provenance = copy.deepcopy(case["reference_provenance"])
    resume["summary"] = ""
    resume["additional"]["technicalSkills"] = []
    provenance["summary_evidence_ids"] = []
    provenance["skills"] = []

    score = _score_against_reference(case, resume, provenance)
    checks = _reference_threshold_checks(score, case["thresholds"])

    assert score.experience_f1 == 1.0
    assert score.evidence_f1 == 1.0
    assert score.technical_skill_recall == 0.0
    assert score.summary_fact_recall == 0.0
    assert score.required_section_recall < 1.0
    assert not checks["technical_skill_recall"]
    assert not checks["technical_skill_f1"]
    assert not checks["summary_fact_recall"]
    assert not checks["required_section_recall"]


def test_generation_reference_scorer_requires_skill_provenance() -> None:
    case = RESUME_GENERATION_CASES[0]
    resume = copy.deepcopy(case["reference_resume"])
    provenance = copy.deepcopy(case["reference_provenance"])
    provenance["skills"] = provenance["skills"][:1]

    score = _score_against_reference(case, resume, provenance)

    assert score.technical_skill_f1 == 1.0
    assert set(score.missing_skill_provenance) == {
        "FastAPI",
        "Redis",
        "Prometheus",
        "Grafana",
        "OpenTelemetry",
    }
    assert not _reference_threshold_checks(score, case["thresholds"])[
        "all_skills_have_provenance"
    ]


def test_generation_reference_scorer_rejects_unrelated_skill_provenance() -> None:
    case = RESUME_GENERATION_CASES[0]
    resume = copy.deepcopy(case["reference_resume"])
    provenance = copy.deepcopy(case["reference_provenance"])
    for item in provenance["skills"][:3]:
        item["evidence_ids"] = [104]

    score = _score_against_reference(case, resume, provenance)

    assert score.evidence_f1 == 1.0
    assert score.missing_skill_provenance == ()
    assert set(score.ungrounded_skill_provenance) == {
        "Python",
        "FastAPI",
        "Redis",
    }
    assert not _reference_threshold_checks(score, case["thresholds"])[
        "all_skills_grounded_to_provenance"
    ]


def test_reference_scorer_rejects_fact_group_missing_from_reference() -> None:
    case = copy.deepcopy(RESUME_GENERATION_CASES[0])
    case["oracle"]["core_fact_groups"].append(["COBOL"])

    with pytest.raises(ValueError, match="core_fact_group"):
        _score_against_reference(
            case,
            case["reference_resume"],
            case["reference_provenance"],
        )


@pytest.mark.parametrize("case", RESUME_GENERATION_CASES, ids=lambda item: item["name"])
def test_generation_reference_scorer_penalizes_hard_negative_selection(
    case: dict[str, Any],
) -> None:
    """每个案例都必须拒绝共享技术词、但不能证明目标能力的经历。"""
    resume = copy.deepcopy(case["reference_resume"])
    provenance = copy.deepcopy(case["reference_provenance"])
    negative_id = case["oracle"]["hard_negative_experience_ids"][0]
    negative = next(
        item for item in case["experiences"] if item["experience_id"] == negative_id
    )
    evidence = negative["evidence"][0]
    description = "，".join(
        str(evidence.get(field, "")).strip()
        for field in ("action", "result")
        if str(evidence.get(field, "")).strip()
    )
    years = f"{negative['start_date']} - {negative.get('end_date', '至今')}"
    if negative["kind"] in {"work", "internship"}:
        section = "workExperience"
        resume[section].append(
            {
                "id": negative_id,
                "title": negative["role"],
                "company": negative["organization"],
                "years": years,
                "description": [description],
            }
        )
    else:
        section = "personalProjects"
        resume[section].append(
            {
                "id": negative_id,
                "name": negative["title"],
                "role": negative["role"],
                "years": years,
                "description": [description],
            }
        )
    provenance["bullets"].append(
        {
            "section": section,
            "item_id": negative_id,
            "bullet_index": 0,
            "evidence_ids": [evidence["evidence_id"]],
        }
    )

    score = _score_against_reference(case, resume, provenance)

    assert score.experience_precision == pytest.approx(2 / 3)
    assert score.experience_recall == 1.0
    assert score.experience_f1 == pytest.approx(0.8)
    assert score.evidence_precision == pytest.approx(2 / 3)
    assert score.evidence_recall == 1.0
    assert score.evidence_f1 == pytest.approx(0.8)
    assert score.unknown_evidence_ids == ()
    assert score.cross_experience_evidence == ()
    checks = _reference_threshold_checks(score, case["thresholds"])
    assert not checks["experience_precision"]
    assert not checks["evidence_precision"]


@pytest.mark.parametrize("case", RESUME_GENERATION_CASES, ids=lambda item: item["name"])
def test_generation_scorer_rejects_each_unsupported_jd_requirement(
    case: dict[str, Any],
) -> None:
    """模型不能靠写入来源不支持的 JD 要求刷覆盖率。"""
    resume = copy.deepcopy(case["reference_resume"])
    unsupported_ids = set(case["oracle"]["unsupported_requirement_ids"])
    unsupported = next(
        requirement["content"]
        for requirement in case["jd_source"]["requirements"]
        if requirement["id"] in unsupported_ids
    )
    resume["summary"] += f" {unsupported}。"

    score = score_generation(
        resume,
        case["experiences"],
        requirement_groups=case["requirement_groups"],
        forbidden_fragments=case["forbidden_fragments"],
    )

    assert score.forbidden_hits


def test_generation_reference_scorer_binds_numbers_to_cited_evidence() -> None:
    case = RESUME_GENERATION_CASES[0]
    resume = copy.deepcopy(case["reference_resume"])
    provenance = copy.deepcopy(case["reference_provenance"])
    resume["workExperience"][0]["description"][0] += "，每月节省 6 小时"

    broad_score = score_generation(
        resume,
        case["experiences"],
        requirement_groups=case["requirement_groups"],
        forbidden_fragments=case["forbidden_fragments"],
    )
    reference_score = _score_against_reference(case, resume, provenance)

    # 6 小时只存在于同一候选人的内部工具难负例中，只有绑定检查能发现错绑。
    assert broad_score.grounded_number_precision == 1.0
    assert reference_score.provenance_grounded_number_precision < 1.0
    assert any(item.endswith(":6小时") for item in reference_score.ungrounded_numbers)


def test_generation_scorers_do_not_treat_m_as_ms() -> None:
    case = RESUME_GENERATION_CASES[0]
    resume = copy.deepcopy(case["reference_resume"])
    provenance = copy.deepcopy(case["reference_provenance"])
    resume["workExperience"][0]["description"][0] = resume["workExperience"][0][
        "description"
    ][0].replace("420ms", "420m")

    broad_score = score_generation(
        resume,
        case["experiences"],
        requirement_groups=case["requirement_groups"],
        forbidden_fragments=case["forbidden_fragments"],
    )
    reference_score = _score_against_reference(case, resume, provenance)

    assert "420m" in broad_score.invented_numbers
    assert broad_score.grounded_number_precision < 1.0
    assert reference_score.provenance_grounded_number_precision < 1.0
    assert any(item.endswith(":420m") for item in reference_score.ungrounded_numbers)


def test_generation_scorers_exclude_source_metadata_numbers() -> None:
    case = RESUME_GENERATION_CASES[0]
    resume = copy.deepcopy(case["reference_resume"])
    provenance = copy.deepcopy(case["reference_provenance"])
    resume["workExperience"][0]["description"][0] += "，2026 年处理 101 个故障"

    broad_score = score_generation(
        resume,
        case["experiences"],
        requirement_groups=case["requirement_groups"],
        forbidden_fragments=case["forbidden_fragments"],
    )
    reference_score = _score_against_reference(case, resume, provenance)

    assert {"101", "2026"} <= set(broad_score.invented_numbers)
    assert broad_score.grounded_number_precision < 1.0
    assert reference_score.provenance_grounded_number_precision < 1.0
    assert any(item.endswith(":101") for item in reference_score.ungrounded_numbers)
    assert any(item.endswith(":2026") for item in reference_score.ungrounded_numbers)


def test_generation_reference_selection_includes_section() -> None:
    case = RESUME_GENERATION_CASES[0]
    resume = copy.deepcopy(case["reference_resume"])
    provenance = copy.deepcopy(case["reference_provenance"])
    moved = resume["workExperience"].pop(0)
    resume["personalProjects"].append(
        {
            "id": moved["id"],
            "name": moved["title"],
            "role": moved["title"],
            "years": moved["years"],
            "description": moved["description"],
        }
    )
    provenance["bullets"][0]["section"] = "personalProjects"

    score = _score_against_reference(case, resume, provenance)

    assert score.experience_precision == 0.5
    assert score.experience_recall == 0.5
    assert score.experience_f1 == 0.5
    assert score.cross_experience_evidence


def test_generation_reference_ignores_and_reports_ghost_provenance() -> None:
    case = RESUME_GENERATION_CASES[0]
    resume = copy.deepcopy(case["reference_resume"])
    provenance = copy.deepcopy(case["reference_provenance"])
    resume["summary"] = ""
    resume["workExperience"] = resume["workExperience"][:1]
    resume["additional"]["technicalSkills"] = ["Python", "FastAPI", "Redis"]
    provenance["summary_evidence_ids"] = [104]
    provenance["bullets"] = [
        provenance["bullets"][0],
        {
            "section": "workExperience",
            "item_id": 4,
            "bullet_index": 0,
            "evidence_ids": [104],
        },
    ]
    provenance["skills"] = [
        *provenance["skills"][:3],
        {"skill": "OpenTelemetry", "evidence_ids": [104]},
    ]

    score = _score_against_reference(case, resume, provenance)

    assert score.evidence_precision == 1.0
    assert score.evidence_recall == 0.5
    assert score.evidence_f1 == pytest.approx(2 / 3)
    assert set(score.ghost_provenance) == {
        "summary",
        "bullet:workExperience:4:0",
        "skill:OpenTelemetry",
    }


def test_resume_generation_golden_cases_have_unique_names() -> None:
    names = [case["name"] for case in RESUME_GENERATION_CASES]

    assert RESUME_GENERATION_CASES
    assert len(names) == len(set(names))


def test_resume_generation_suite_stays_representative() -> None:
    """防止套件再次退化成一个容易刷满分的单案例。"""
    assert len(RESUME_GENERATION_CASES) >= 5
    allowed_reference_statuses = {
        "synthetic_fixture_not_expert_reviewed",
        "expert_reviewed",
    }
    assert len({case["jd_source"]["type"] for case in RESUME_GENERATION_CASES}) >= 4
    assert {
        section
        for case in RESUME_GENERATION_CASES
        for section in ("workExperience", "personalProjects")
        if case["reference_resume"].get(section)
    } == {"workExperience", "personalProjects"}
    assert all(
        case["oracle"]["unsupported_requirement_ids"]
        for case in RESUME_GENERATION_CASES
    )
    assert all(
        case["oracle"]["summary_fact_groups"] for case in RESUME_GENERATION_CASES
    )
    assert all(
        case["reference_resume"].get("additional", {}).get("technicalSkills")
        for case in RESUME_GENERATION_CASES
    )
    profile_ids = [case["candidate_profile_id"] for case in RESUME_GENERATION_CASES]
    assert len(profile_ids) == len(set(profile_ids))

    experience_sets: list[set[int]] = []
    evidence_sets: list[set[int]] = []
    for case in RESUME_GENERATION_CASES:
        reference_review = case["reference_review"]
        assert case["reference_status"] in allowed_reference_statuses
        assert reference_review["status"] == case["reference_status"]
        assert isinstance(reference_review["expert_reviewed"], bool)
        assert isinstance(reference_review["blind_reviewed"], bool)
        assert reference_review["reviewer_role"]
        assert reference_review["review_method"]
        if reference_review["expert_reviewed"]:
            assert case["reference_status"] == "expert_reviewed"
            assert reference_review["reviewed_at"]
            assert reference_review["reviewer_role"] != "fixture_author"
        else:
            assert case["reference_status"] == "synthetic_fixture_not_expert_reviewed"
            assert reference_review["reviewed_at"] is None

        experiences = case["experiences"]
        experience_ids = {item["experience_id"] for item in experiences}
        evidence_ids = {
            evidence["evidence_id"]
            for item in experiences
            for evidence in item["evidence"]
        }
        selected_ids = set(case["oracle"]["expected_experience_ids"])
        hard_negative_ids = set(case["oracle"]["hard_negative_experience_ids"])
        hard_negative_evidence_ids = set(case["oracle"]["hard_negative_evidence_ids"])
        supported_requirement_ids = set(case["oracle"]["supported_requirement_ids"])
        unsupported_requirement_ids = set(case["oracle"]["unsupported_requirement_ids"])
        requirement_ids = {
            requirement["id"] for requirement in case["jd_source"]["requirements"]
        }
        reference_skills = set(
            case["reference_resume"]["additional"]["technicalSkills"]
        )
        hard_negative_skills = {
            skill
            for item in experiences
            if item["experience_id"] in hard_negative_ids
            for skill in item["technologies"]
        }

        assert len(experiences) >= 3
        assert len(selected_ids) >= 2
        assert selected_ids < experience_ids
        assert hard_negative_ids <= experience_ids
        assert hard_negative_evidence_ids <= evidence_ids
        assert (
            supported_requirement_ids | unsupported_requirement_ids == requirement_ids
        )
        assert supported_requirement_ids.isdisjoint(unsupported_requirement_ids)
        assert selected_ids.isdisjoint(hard_negative_ids)
        assert set(case["oracle"]["expected_evidence_ids"]).isdisjoint(
            hard_negative_evidence_ids
        )
        assert reference_skills & hard_negative_skills
        experience_sets.append(experience_ids)
        evidence_sets.append(evidence_ids)

    for index, experience_ids in enumerate(experience_sets):
        assert all(
            experience_ids.isdisjoint(other) for other in experience_sets[index + 1 :]
        )
    for index, evidence_ids in enumerate(evidence_sets):
        assert all(
            evidence_ids.isdisjoint(other) for other in evidence_sets[index + 1 :]
        )


class _OracleRetriever:
    """提供完整候选，并让检索元数据对所有 Evidence 保持中性。"""

    async def retrieve(
        self,
        tasks: list[SearchTask],
        documents: list[Any],
    ) -> list[RetrievedEvidence]:
        task_ids = [task.task_id for task in tasks]
        return [
            RetrievedEvidence(
                document=document,
                retrieval_score=0.5,
                task_ids=task_ids,
            )
            for document in documents
        ]


async def test_oracle_retriever_does_not_leak_reference_order() -> None:
    tasks = [
        SearchTask(
            task_id="oracle-task-a",
            coverage_item_ids=["coverage-a"],
            intent="responsibility",
            query="候选事实 A",
        ),
        SearchTask(
            task_id="oracle-task-b",
            coverage_item_ids=["coverage-b"],
            intent="result_evidence",
            query="候选事实 B",
        ),
    ]
    expected_task_ids = [task.task_id for task in tasks]
    hard_negative_positions: list[int] = []

    for case in RESUME_GENERATION_CASES:
        experiences = [
            ExperienceSnapshot.model_validate(item) for item in case["experiences"]
        ]
        retrieved = await _OracleRetriever().retrieve(
            tasks,
            build_documents(experiences),
        )
        assert {item.retrieval_score for item in retrieved} == {0.5}
        assert all(item.task_ids == expected_task_ids for item in retrieved)

        merged = merge_retrieval_rounds([], retrieved)
        ordered_ids = [item.document.evidence_id for item in merged]
        hard_negative_id = case["oracle"]["hard_negative_evidence_ids"][0]
        hard_negative_positions.append(ordered_ids.index(hard_negative_id))

    assert len(set(hard_negative_positions)) >= 2


async def _run_generation_graph(case: dict[str, Any], model: Any) -> dict[str, Any]:
    """以固定完整候选运行生产 Graph，隔离召回模型波动。"""
    source = JDAnalysisSourceSnapshot.model_validate(case["jd_source"])
    experiences = [
        ExperienceSnapshot.model_validate(item) for item in case["experiences"]
    ]
    graph = build_resume_generation_graph(
        ResumeGenerationGraphDependencies(
            model=model,
            retriever=_OracleRetriever(),
        )
    ).compile()
    return await graph.ainvoke(
        {
            "jd_source": source,
            "experiences": experiences,
            "constraints": ResumeConstraints.model_validate(case["constraints"]),
        }
    )


@pytest.mark.parametrize("case", RESUME_GENERATION_CASES, ids=lambda item: item["name"])
async def test_rule_based_generation_graph_preserves_safety_invariants(
    case: dict[str, Any],
) -> None:
    """规则降级只做安全 smoke，不伪装成参考答案质量评测。"""
    state = await _run_generation_graph(case, RuleBasedResumeGenerationModel())
    resume = state["resume_data"].model_dump(mode="json")
    provenance = state["provenance"].model_dump(mode="json")
    score = score_generation(
        resume,
        case["experiences"],
        requirement_groups=case["requirement_groups"],
        forbidden_fragments=case["forbidden_fragments"],
    )
    reference_score = _score_against_reference(case, resume, provenance)
    assert state["validation"].valid
    assert score.grounded_number_precision == 1.0
    assert score.empty_bullet_count == 0
    assert not score.forbidden_hits
    assert reference_score.provenance_grounded_number_precision == 1.0
    assert not reference_score.ungrounded_numbers
    assert not reference_score.unknown_evidence_ids
    assert not reference_score.cross_experience_evidence
    assert not reference_score.missing_bullet_provenance
    assert not reference_score.ungrounded_skill_provenance
    assert not reference_score.ghost_provenance


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.eval
@pytest.mark.parametrize("case", RESUME_GENERATION_CASES, ids=lambda item: item["name"])
async def test_resume_generation_quality_meets_golden_thresholds(
    case: dict[str, Any],
    generation_suite_records: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """在 Oracle 召回下评估生成质量；不把结果冒充端到端召回质量。"""
    config = require_llm()  # 必须先 gate。
    _disable_quality_eval_provider_retries(monkeypatch)
    try:
        judge_config = get_eval_judge_config(config)
    except ValueError as error:
        pytest.fail(f"Judge 配置无效，尚未调用生成模型：{error}")
    judge_relation = judge_model_relation(config, judge_config)
    judge_is_independent = judge_relation == "independent_model"
    thresholds = case["thresholds"]
    case_metadata = {
        "name": case["name"],
        "version": case["version"],
        "candidate_profile_id": case["candidate_profile_id"],
        "reference_status": case["reference_status"],
        "reference_review": case["reference_review"],
        "hard_negative_experience_ids": case["oracle"]["hard_negative_experience_ids"],
    }
    case_input = {
        "jd_source": case["jd_source"],
        "experiences": case["experiences"],
        "constraints": case["constraints"],
        "oracle": case["oracle"],
    }
    report_model = {
        **model_metadata(config),
        "evaluation_scope": "generation_only_with_oracle_retrieval",
        "oracle_retrieval_policy": "complete_pool_uniform_score_all_tasks",
        "judge_model": model_metadata(judge_config),
        "judge_relation": judge_relation,
        "judge_role": "secondary_to_deterministic_gates",
        "model_call_contract": _MODEL_CALL_CONTRACT,
    }
    model = FallbackResumeGenerationModel(
        LangChainResumeGenerationModel(),
        RuleBasedResumeGenerationModel(),
    )
    try:
        state = await _run_generation_graph(case, model)
    except Exception as error:  # noqa: BLE001 - 先保留模型/结构失败现场
        error_text = f"{type(error).__name__}: {error}"
        failed_case = {
            **case_metadata,
            "input": case_input,
            "error": error_text,
            "fallback_errors": model.fallback_errors,
        }
        failed_summary = _failed_generation_summary(
            stage="generation_graph",
            error=error_text,
            fallback_events=model.fallback_events,
            fallback_errors=model.fallback_errors,
        )
        path = write_quality_report(
            "resume-generation",
            model=report_model,
            thresholds=thresholds,
            cases=[failed_case],
            summary=failed_summary,
            report_version="2",
        )
        _record_generation_suite_result(
            generation_suite_records,
            case=failed_case,
            summary=failed_summary,
            model=report_model,
            thresholds=thresholds,
        )
        pytest.fail(f"简历生成评测无法完成；报告：{path}\n{error}")

    resume = state["resume_data"].model_dump(mode="json")
    provenance = state["provenance"].model_dump(mode="json")
    if model.fallback_events:
        error_text = "生成阶段触发 fallback；未调用 Judge"
        failed_case = {
            **case_metadata,
            "input": case_input,
            "error": error_text,
            "fallback_events": model.fallback_events,
            "fallback_errors": model.fallback_errors,
            "plan": state["plan"].model_dump(mode="json"),
            "validation": state["validation"].model_dump(mode="json"),
            "resume": resume,
            "provenance": provenance,
            "pipeline_diagnostics": _pipeline_diagnostics(state),
        }
        failed_summary = _failed_generation_summary(
            stage="fallback",
            error=error_text,
            fallback_events=model.fallback_events,
            fallback_errors=model.fallback_errors,
        )
        path = write_quality_report(
            "resume-generation",
            model=report_model,
            thresholds=thresholds,
            cases=[failed_case],
            summary=failed_summary,
            report_version="2",
        )
        _record_generation_suite_result(
            generation_suite_records,
            case=failed_case,
            summary=failed_summary,
            model=report_model,
            thresholds=thresholds,
        )
        pytest.fail(f"简历生成评测触发 fallback，未调用 Judge；报告：{path}")

    score = None
    reference_score = None
    try:
        score = score_generation(
            resume,
            case["experiences"],
            requirement_groups=case["requirement_groups"],
            forbidden_fragments=case["forbidden_fragments"],
        )
        reference_score = _score_against_reference(case, resume, provenance)
        judgments = await judge_outputs(
            "resume-generation",
            [
                {
                    "case_name": case["name"],
                    "task": {
                        "jd_source": case["jd_source"],
                        "supported_requirement_ids": case["oracle"][
                            "supported_requirement_ids"
                        ],
                        "unsupported_requirement_ids": case["oracle"][
                            "unsupported_requirement_ids"
                        ],
                    },
                    "source": {"experiences": case["experiences"]},
                    "reference": {
                        "resume": case["reference_resume"],
                        "provenance": case["reference_provenance"],
                    },
                    "candidate": {
                        "resume": resume,
                        "provenance": provenance,
                    },
                }
            ],
            config=judge_config,
        )
    except Exception as error:  # noqa: BLE001 - 先保留模型/结构失败现场
        error_text = f"{type(error).__name__}: {error}"
        failed_case: dict[str, Any] = {
            **case_metadata,
            "input": case_input,
            "error": error_text,
            "fallback_errors": model.fallback_errors,
            "plan": state["plan"].model_dump(mode="json"),
            "validation": state["validation"].model_dump(mode="json"),
            "resume": resume,
            "provenance": provenance,
            "pipeline_diagnostics": _pipeline_diagnostics(state),
        }
        if score is not None:
            failed_case["objective_metrics"] = asdict(score)
        if reference_score is not None:
            failed_case["reference_metrics"] = asdict(reference_score)
        failed_summary = _failed_generation_summary(
            stage="scoring_or_judge",
            error=error_text,
            fallback_events=model.fallback_events,
            fallback_errors=model.fallback_errors,
        )
        path = write_quality_report(
            "resume-generation",
            model=report_model,
            thresholds=thresholds,
            cases=[failed_case],
            summary=failed_summary,
            report_version="2",
        )
        _record_generation_suite_result(
            generation_suite_records,
            case=failed_case,
            summary=failed_summary,
            model=report_model,
            thresholds=thresholds,
        )
        pytest.fail(f"简历生成评测无法完成；报告：{path}\n{error}")

    validation = state["validation"].model_dump(mode="json")
    case_report = {
        **case_metadata,
        "input": case_input,
        "evaluation_scope": "generation_only_with_oracle_retrieval",
        "oracle_retrieval_policy": "complete_pool_uniform_score_all_tasks",
        "judge_relation": judge_relation,
        "objective_metrics": asdict(score),
        "reference_metrics": asdict(reference_score),
        "judge": judgments[0].model_dump(mode="json"),
        "plan": state["plan"].model_dump(mode="json"),
        "validation": validation,
        "resume": resume,
        "provenance": provenance,
        "pipeline_diagnostics": _pipeline_diagnostics(state),
        "fallback_errors": model.fallback_errors,
        "reference": {
            "resume": case["reference_resume"],
            "provenance": case["reference_provenance"],
        },
    }
    checks = {
        "validation_valid": validation["valid"] is True,
        "model_validation_status": (
            validation["model_validation_status"]
            == thresholds["model_validation_status"]
        ),
        "requirement_coverage": (
            score.requirement_coverage >= thresholds["minimum_requirement_coverage"]
        ),
        "grounded_number_precision": (
            score.grounded_number_precision
            >= thresholds["minimum_grounded_number_precision"]
        ),
        "minimum_bullets": score.bullet_count >= thresholds["minimum_bullets"],
        "has_resume_content": _has_resume_content(resume),
        "no_empty_bullets": score.empty_bullet_count == 0,
        "no_forbidden_facts": not score.forbidden_hits,
        "judge_grounding": (
            judgments[0].grounding >= thresholds["minimum_judge_grounding"]
        ),
        "judge_overall": (judgments[0].overall >= thresholds["minimum_judge_overall"]),
        "judge_no_unsupported_claims": not judgments[0].unsupported_claims,
        "no_fallback": not model.fallback_events,
        **{
            f"reference_{name}": passed
            for name, passed in _reference_threshold_checks(
                reference_score,
                thresholds,
            ).items()
        },
    }
    dimensions = _quality_dimensions(checks)
    summary = {
        "passed": all(checks.values()),
        **dimensions,
        "judge_is_independent": judge_is_independent,
        "checks": checks,
        "validation_valid": validation["valid"],
        "requirement_coverage": score.requirement_coverage,
        "grounded_number_precision": score.grounded_number_precision,
        "experience_f1": reference_score.experience_f1,
        "evidence_f1": reference_score.evidence_f1,
        "technical_skill_f1": reference_score.technical_skill_f1,
        "summary_fact_recall": reference_score.summary_fact_recall,
        "required_section_recall": reference_score.required_section_recall,
        "core_fact_recall": reference_score.core_fact_recall,
        "provenance_grounded_number_precision": (
            reference_score.provenance_grounded_number_precision
        ),
        "judge_overall": judgments[0].overall,
        "fallback_events": model.fallback_events,
        "fallback_errors": model.fallback_errors,
    }
    path = write_quality_report(
        "resume-generation",
        model=report_model,
        thresholds=thresholds,
        cases=[case_report],
        summary=summary,
        report_version="2",
    )
    _record_generation_suite_result(
        generation_suite_records,
        case=case_report,
        summary=summary,
        model=report_model,
        thresholds=thresholds,
    )
    failed_checks = [name for name, passed in checks.items() if not passed]
    assert not failed_checks, (path, failed_checks)
