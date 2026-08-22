"""JD 导入评分器与生产结构化提取质量评测。"""

from dataclasses import asdict
from statistics import mean

import pytest

from app.jd_import.agent.evidence import assess_candidates
from app.jd_import.agent.model import ExtractionRequest, LangChainJDImportModel
from app.jd_import.agent.types import ImportSource
from tests.evals.golden.jd_import_cases import JD_IMPORT_CASES
from tests.evals.quality_eval_support import model_metadata, require_llm
from tests.evals.quality_report import write_quality_report
from tests.evals.quality_scorers import score_jd_import


def _good_jd_import_output() -> dict:
    source_id = "source:text:0"

    def fact(value: str, quote: str) -> dict:
        return {"value": value, "source_id": source_id, "quote": quote}

    return {
        "candidates": [
            {
                "jd_key": "jd-1",
                "source_url": None,
                "company": fact("云舟网络", "云舟网络"),
                "job_name": fact("高级后端工程师", "高级后端工程师"),
                "type": fact("后端", "后端工程师"),
                "location": fact("上海", "工作地点上海"),
                "requirements": [
                    {
                        **fact(
                            "Python 与 FastAPI 高并发 API",
                            "Python 后端开发经验，能够使用 FastAPI 设计高并发 API",
                        ),
                        "priority": "required",
                        "sort_order": 0,
                    },
                    {
                        **fact("Redis 缓存与性能优化", "熟悉 Redis 缓存与性能优化"),
                        "priority": "required",
                        "sort_order": 1,
                    },
                    {
                        **fact(
                            "建设链路追踪和告警，降低故障恢复时间",
                            "能建设指标、链路追踪和告警，降低线上故障恢复时间",
                        ),
                        "priority": "required",
                        "sort_order": 2,
                    },
                    {
                        **fact("Kubernetes 容器编排", "了解 Kubernetes 容器编排"),
                        "priority": "preferred",
                        "sort_order": 3,
                    },
                ],
                "missing_fields": [],
            }
        ],
        "conflicts": [],
        "errors": [],
    }


def test_jd_import_scorer_accepts_source_backed_requirements() -> None:
    case = JD_IMPORT_CASES[0]
    score = score_jd_import(
        _good_jd_import_output(),
        source_contents={case["source_id"]: case["text"]},
        expected_fields=case["expected_fields"],
        expected_requirements=case["expected_requirements"],
        forbidden_fragments=case["forbidden_fragments"],
        expected_candidate_count=case["expected_candidate_count"],
    )

    assert score.field_accuracy == 1.0
    assert score.requirement_recall == 1.0
    assert score.priority_accuracy == 1.0
    assert score.quote_grounding_rate == 1.0
    assert score.candidate_count_matches is True
    assert score.assessment_error_count == 0
    assert score.missing_fields == ()


def test_jd_import_scorer_finds_bad_split_quote_and_injection() -> None:
    case = JD_IMPORT_CASES[0]
    bad = _good_jd_import_output()
    candidate = bad["candidates"][0]
    candidate["company"] = {
        "value": "字节跳动",
        "source_id": "source:missing",
        "quote": "字节跳动",
    }
    candidate["requirements"] = candidate["requirements"][:1]
    candidate["requirements"][0]["priority"] = "normal"
    candidate["missing_fields"] = ["location"]
    bad["errors"] = [{"code": "unknown_source", "field": "company"}]
    score = score_jd_import(
        bad,
        source_contents={case["source_id"]: case["text"]},
        expected_fields=case["expected_fields"],
        expected_requirements=case["expected_requirements"],
        forbidden_fragments=case["forbidden_fragments"],
        expected_candidate_count=case["expected_candidate_count"],
    )

    assert score.field_accuracy < 1.0
    assert score.requirement_recall == 0.25
    assert score.priority_accuracy == 0.0
    assert score.quote_grounding_rate < 1.0
    assert score.assessment_error_count == 1
    assert score.missing_fields == ("location",)
    assert score.forbidden_hits == ("字节跳动",)


def test_jd_import_golden_cases_have_unique_names() -> None:
    names = [case["name"] for case in JD_IMPORT_CASES]

    assert len(JD_IMPORT_CASES) >= 2
    assert len(names) == len(set(names))


@pytest.mark.eval
async def test_jd_import_quality_meets_golden_thresholds() -> None:
    """运行生产 JD 结构化模型和 Evidence assessor，检查要求与原文引用。"""
    config = require_llm()  # 必须先 gate。
    thresholds = {
        "minimum_field_accuracy": 1.0,
        "minimum_requirement_recall": 0.90,
        "minimum_priority_accuracy": 0.75,
        "minimum_quote_grounding_rate": 1.0,
        "assessment_errors": 0,
        "conflicts": 0,
        "forbidden_hits": 0,
    }
    reports: list[dict] = []
    model = LangChainJDImportModel()
    for case in JD_IMPORT_CASES:
        source = ImportSource(
            source_id=case["source_id"],
            type="text",
            content=case["text"],
        )
        try:
            extraction = await model.extract(ExtractionRequest(sources=[source]))
            assessment = assess_candidates(
                [source], extraction.candidates, extraction.conflicts
            )
            output = assessment.model_dump(mode="json")
            score = score_jd_import(
                output,
                source_contents={case["source_id"]: case["text"]},
                expected_fields=case["expected_fields"],
                expected_requirements=case["expected_requirements"],
                forbidden_fragments=case["forbidden_fragments"],
                expected_candidate_count=case["expected_candidate_count"],
            )
            reports.append(
                {
                    "name": case["name"],
                    "metrics": asdict(score),
                    "raw_extraction": extraction.model_dump(mode="json"),
                    "assessment": output,
                }
            )
        except Exception as error:  # noqa: BLE001 - 继续其余案例并保留报告
            reports.append(
                {
                    "name": case["name"],
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    successful = [item for item in reports if "metrics" in item]
    summary = {
        "completed_cases": len(successful),
        "total_cases": len(JD_IMPORT_CASES),
        "mean_field_accuracy": mean(
            item["metrics"]["field_accuracy"] for item in successful
        )
        if successful
        else 0.0,
        "mean_requirement_recall": mean(
            item["metrics"]["requirement_recall"] for item in successful
        )
        if successful
        else 0.0,
        "mean_quote_grounding_rate": mean(
            item["metrics"]["quote_grounding_rate"] for item in successful
        )
        if successful
        else 0.0,
    }
    path = write_quality_report(
        "jd-import",
        model=model_metadata(config),
        thresholds=thresholds,
        cases=reports,
        summary=summary,
    )
    assert len(successful) == len(JD_IMPORT_CASES), path
    for item in successful:
        metrics = item["metrics"]
        assert metrics["field_accuracy"] >= thresholds["minimum_field_accuracy"], path
        assert (
            metrics["requirement_recall"] >= thresholds["minimum_requirement_recall"]
        ), path
        assert (
            metrics["priority_accuracy"] >= thresholds["minimum_priority_accuracy"]
        ), path
        assert (
            metrics["quote_grounding_rate"]
            >= thresholds["minimum_quote_grounding_rate"]
        ), path
        assert metrics["candidate_count_matches"] is True, path
        assert metrics["assessment_error_count"] == 0, path
        assert metrics["conflict_count"] == 0, path
        assert not metrics["missing_fields"], path
        assert not metrics["forbidden_hits"], path
