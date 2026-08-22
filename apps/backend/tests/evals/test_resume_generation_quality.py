"""简历生成评分器与生产 Graph 文案质量评测。"""

from dataclasses import asdict
from typing import Any

import pytest

from app.resume_generation.graph import (
    ResumeGenerationGraphDependencies,
    build_resume_generation_graph,
)
from app.resume_generation.model import (
    FallbackResumeGenerationModel,
    LangChainResumeGenerationModel,
    RuleBasedResumeGenerationModel,
)
from app.resume_generation.schemas import (
    ExperienceSnapshot,
    JDAnalysisSourceSnapshot,
    ResumeConstraints,
    RetrievedEvidence,
    SearchTask,
)
from tests.evals.golden.resume_generation_cases import RESUME_GENERATION_CASES
from tests.evals.quality_eval_support import (
    judge_outputs,
    model_metadata,
    require_llm,
)
from tests.evals.quality_report import write_quality_report
from tests.evals.quality_scorers import score_generation


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


def test_resume_generation_golden_cases_have_unique_names() -> None:
    names = [case["name"] for case in RESUME_GENERATION_CASES]

    assert RESUME_GENERATION_CASES
    assert len(names) == len(set(names))


class _OracleRetriever:
    """给生成评测提供完整黄金候选，隔离召回误差。"""

    async def retrieve(
        self,
        tasks: list[SearchTask],
        documents: list[Any],
    ) -> list[RetrievedEvidence]:
        task_ids = [task.task_id for task in tasks]
        return [
            RetrievedEvidence(
                document=document,
                retrieval_score=max(0.5, 0.95 - index * 0.03),
                task_ids=task_ids,
            )
            for index, document in enumerate(documents)
        ]


@pytest.mark.eval
async def test_resume_generation_quality_meets_golden_thresholds() -> None:
    """运行生产 auto 生成链路，召回由 oracle 固定，单独衡量规划与文案。"""
    config = require_llm()  # 必须先 gate。
    thresholds = {
        "validation_valid": True,
        "minimum_requirement_coverage": 0.75,
        "minimum_grounded_number_precision": 1.0,
        "minimum_bullets": 2,
        "minimum_judge_overall": 4,
        "minimum_judge_grounding": 4,
    }
    case = RESUME_GENERATION_CASES[0]
    source = JDAnalysisSourceSnapshot.model_validate(case["jd_source"])
    experiences = [
        ExperienceSnapshot.model_validate(item) for item in case["experiences"]
    ]
    model = FallbackResumeGenerationModel(
        LangChainResumeGenerationModel(),
        RuleBasedResumeGenerationModel(),
    )
    try:
        graph = build_resume_generation_graph(
            ResumeGenerationGraphDependencies(
                model=model,
                retriever=_OracleRetriever(),
            )
        ).compile()
        state = await graph.ainvoke(
            {
                "jd_source": source,
                "experiences": experiences,
                "constraints": ResumeConstraints(
                    max_work_experiences=3,
                    max_project_experiences=2,
                    max_bullets_per_experience=3,
                    top_k_per_task=6,
                    max_search_rounds=1,
                    min_coverage_ratio=0.70,
                ),
            }
        )
        resume = state["resume_data"].model_dump(mode="json")
        score = score_generation(
            resume,
            case["experiences"],
            requirement_groups=case["requirement_groups"],
            forbidden_fragments=case["forbidden_fragments"],
        )
        judgments = await judge_outputs(
            "resume-generation",
            [
                {
                    "case_name": case["name"],
                    "task": case["jd_source"],
                    "source": case["experiences"],
                    "candidate": resume,
                }
            ],
        )
    except Exception as error:  # noqa: BLE001 - 先保留模型/结构失败现场
        path = write_quality_report(
            "resume-generation",
            model=model_metadata(config),
            thresholds=thresholds,
            cases=[{"name": case["name"], "error": str(error)}],
            summary={"passed": False, "fallback_events": model.fallback_events},
        )
        pytest.fail(f"简历生成评测无法完成；报告：{path}\n{error}")

    validation = state["validation"].model_dump(mode="json")
    case_report = {
        "name": case["name"],
        "objective_metrics": asdict(score),
        "judge": judgments[0].model_dump(mode="json"),
        "plan": state["plan"].model_dump(mode="json"),
        "validation": validation,
        "resume": resume,
    }
    summary = {
        "validation_valid": validation["valid"],
        "requirement_coverage": score.requirement_coverage,
        "grounded_number_precision": score.grounded_number_precision,
        "judge_overall": judgments[0].overall,
        "fallback_events": model.fallback_events,
    }
    path = write_quality_report(
        "resume-generation",
        model=model_metadata(config),
        thresholds=thresholds,
        cases=[case_report],
        summary=summary,
    )
    assert validation["valid"] is True, path
    assert score.requirement_coverage >= thresholds["minimum_requirement_coverage"], (
        path
    )
    assert (
        score.grounded_number_precision
        >= thresholds["minimum_grounded_number_precision"]
    ), path
    assert score.bullet_count >= thresholds["minimum_bullets"], path
    assert score.empty_bullet_count == 0, path
    assert not score.forbidden_hits, path
    assert judgments[0].grounding >= thresholds["minimum_judge_grounding"], path
    assert judgments[0].overall >= thresholds["minimum_judge_overall"], path
    assert not judgments[0].unsupported_claims, path
