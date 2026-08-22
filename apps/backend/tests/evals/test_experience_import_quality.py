"""经历文本导入评分器与生产提取质量评测。"""

from dataclasses import asdict
from statistics import mean

import pytest

from app.experience.services import experience_text_extractor as extractor_module
from app.experience.services.experience_text_extractor import ExperienceTextExtractor
from tests.evals.golden.experience_import_cases import EXPERIENCE_IMPORT_CASES
from tests.evals.quality_eval_support import model_metadata, require_llm
from tests.evals.quality_report import write_quality_report
from tests.evals.quality_scorers import score_import


def _good_import_output() -> dict:
    return {
        "experience_id": None,
        "experience": {
            "kind": "work",
            "title": "支付平台稳定性改造",
            "organization": "星河科技",
            "role": "后端工程师",
            "location": "上海",
            "start_date": "2024-03",
            "end_date": "2025-01",
            "is_current": False,
            "background": "大促期间支付网关超时",
            "technologies": ["Python", "FastAPI", "Redis"],
            "tags": ["性能优化"],
            "notes": None,
            "expected_field_revisions": {},
        },
        "evidence_items": [
            {
                "background": "大促接口超时",
                "action": "重构支付查询链路",
                "result": "P99 从 420ms 降至 180ms，吞吐量提升 35%",
            },
            {
                "background": "定位耗时",
                "action": "建设告警和链路追踪",
                "result": "MTTR 降至 18 分钟",
            },
        ],
        "expected_collection_revision": None,
    }


def test_import_scorer_accepts_grounded_complete_draft() -> None:
    case = EXPERIENCE_IMPORT_CASES[0]
    score = score_import(
        _good_import_output(),
        exact_fields=case["exact_fields"],
        required_fragments=case["required_fragments"],
        required_list_items=case["required_list_items"],
        forbidden_fragments=case["forbidden_fragments"],
        expected_evidence_count=case["expected_evidence_count"],
    )

    assert score.exact_field_accuracy == 1.0
    assert score.fact_recall == 1.0
    assert score.evidence_count_matches is True
    assert score.forbidden_hits == ()


def test_import_scorer_finds_field_loss_and_hallucination() -> None:
    case = EXPERIENCE_IMPORT_CASES[0]
    bad = _good_import_output()
    bad["experience"]["organization"] = "字节跳动"
    bad["experience"]["technologies"] = ["Python"]
    bad["evidence_items"] = [
        {
            "background": None,
            "action": "带领20人团队完成优化",
            "result": "延迟改善",
        }
    ]
    score = score_import(
        bad,
        exact_fields=case["exact_fields"],
        required_fragments=case["required_fragments"],
        required_list_items=case["required_list_items"],
        forbidden_fragments=case["forbidden_fragments"],
        expected_evidence_count=case["expected_evidence_count"],
    )

    assert "experience.organization" in score.field_mismatches
    assert score.fact_recall < 0.5
    assert score.evidence_count_matches is False
    assert set(score.forbidden_hits) == {"20人团队", "字节跳动"}


def test_experience_import_golden_cases_have_unique_names() -> None:
    names = [case["name"] for case in EXPERIENCE_IMPORT_CASES]

    assert len(EXPERIENCE_IMPORT_CASES) >= 2
    assert len(names) == len(set(names))


@pytest.mark.eval
async def test_experience_import_quality_meets_golden_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """直接运行生产 ExperienceTextExtractor，检查事实映射和指令注入隔离。"""
    config = require_llm()  # 必须先 gate，下面才可构造模型请求。
    thresholds = {
        "minimum_exact_field_accuracy": 0.90,
        "minimum_fact_recall": 0.90,
        "forbidden_hits": 0,
        "evidence_count_must_match": True,
    }
    reports: list[dict] = []
    extractor = ExperienceTextExtractor()
    for case in EXPERIENCE_IMPORT_CASES:
        monkeypatch.setattr(
            extractor_module,
            "get_content_language",
            lambda language=case["language"]: language,
        )
        try:
            output = (await extractor.extract(case["text"])).model_dump(mode="json")
            score = score_import(
                output,
                exact_fields=case["exact_fields"],
                required_fragments=case["required_fragments"],
                required_list_items=case["required_list_items"],
                forbidden_fragments=case["forbidden_fragments"],
                expected_evidence_count=case["expected_evidence_count"],
            )
            reports.append(
                {
                    "name": case["name"],
                    "metrics": asdict(score),
                    "output": output,
                }
            )
        except Exception as error:  # noqa: BLE001 - 继续其他黄金案例并统一报告
            reports.append(
                {
                    "name": case["name"],
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    successful = [item for item in reports if "metrics" in item]
    summary = {
        "completed_cases": len(successful),
        "total_cases": len(EXPERIENCE_IMPORT_CASES),
        "mean_exact_field_accuracy": mean(
            item["metrics"]["exact_field_accuracy"] for item in successful
        )
        if successful
        else 0.0,
        "mean_fact_recall": mean(item["metrics"]["fact_recall"] for item in successful)
        if successful
        else 0.0,
        "forbidden_hit_count": sum(
            len(item["metrics"]["forbidden_hits"]) for item in successful
        ),
    }
    path = write_quality_report(
        "experience-import",
        model=model_metadata(config),
        thresholds=thresholds,
        cases=reports,
        summary=summary,
    )
    assert len(successful) == len(EXPERIENCE_IMPORT_CASES), path
    for item in successful:
        metrics = item["metrics"]
        assert (
            metrics["exact_field_accuracy"]
            >= thresholds["minimum_exact_field_accuracy"]
        ), path
        assert metrics["fact_recall"] >= thresholds["minimum_fact_recall"], path
        assert not metrics["forbidden_hits"], path
        assert metrics["evidence_count_matches"] is True, path
