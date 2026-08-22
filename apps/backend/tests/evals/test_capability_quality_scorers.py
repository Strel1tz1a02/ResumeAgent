"""四项能力评分器的反剧场测试：好样本通过，坏样本必须被识别。"""

from dataclasses import asdict

import pytest

from tests.evals.golden.quality_cases import (
    GENERATION_CASE,
    IMPORT_CASES,
    JD_IMPORT_CASES,
    RETRIEVAL_CASES,
    RETRIEVAL_EXPERIENCES,
    REWRITE_CASES,
)
from tests.evals.quality_scorers import (
    score_generation,
    score_import,
    score_jd_import,
    score_retrieval,
    score_rewrite,
)


def test_retrieval_scorer_rewards_early_relevant_result() -> None:
    score = score_retrieval([105, 101, 106], {101}, k=3)

    assert score.hit_at_k is True
    assert score.recall_at_k == 1.0
    assert score.reciprocal_rank == 0.5
    assert 0.6 < score.ndcg_at_k < 0.7


def test_retrieval_scorer_rejects_missed_result() -> None:
    score = score_retrieval([105, 106, 103], {101}, k=3)

    assert asdict(score) == {
        "precision_at_k": 0.0,
        "recall_at_k": 0.0,
        "reciprocal_rank": 0.0,
        "ndcg_at_k": 0.0,
        "hit_at_k": False,
    }


def test_retrieval_scorer_rejects_empty_oracle() -> None:
    with pytest.raises(ValueError, match="relevant_ids"):
        score_retrieval([101], set(), k=3)


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
    case = IMPORT_CASES[0]
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
    case = IMPORT_CASES[0]
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
    score = score_generation(
        _good_generated_resume(),
        GENERATION_CASE["experiences"],
        requirement_groups=GENERATION_CASE["requirement_groups"],
        forbidden_fragments=GENERATION_CASE["forbidden_fragments"],
    )

    assert score.requirement_coverage == 0.75
    assert score.grounded_number_precision == 1.0
    assert score.bullet_count == 2
    assert score.invented_numbers == ()
    assert score.forbidden_hits == ()


def test_generation_scorer_finds_unsupported_content() -> None:
    bad = _good_generated_resume()
    bad["summary"] = "字节跳动 Kubernetes 架构师，营收翻倍。"
    bad["workExperience"][0]["description"] = ["性能提升 99%"]
    score = score_generation(
        bad,
        GENERATION_CASE["experiences"],
        requirement_groups=GENERATION_CASE["requirement_groups"],
        forbidden_fragments=GENERATION_CASE["forbidden_fragments"],
    )

    # 关键词覆盖本身会被虚构内容“刷高”，因此必须与 grounding/forbidden 联合判定。
    assert score.requirement_coverage == 0.75
    assert "99%" in score.invented_numbers
    assert score.grounded_number_precision == 0.0
    assert {"字节跳动", "营收翻倍", "Kubernetes"} <= set(score.forbidden_hits)


def test_rewrite_scorer_accepts_faithful_improvement() -> None:
    case = REWRITE_CASES[0]
    score = score_rewrite(
        case["current_content"],
        case["user_request"],
        "负责支付网关性能治理，将 P99 延迟从 420ms 降至 180ms，吞吐量提升 35%。",
        required_fragments=case["required_fragments"],
        forbidden_fragments=case["forbidden_fragments"],
    )

    assert score.fact_recall == 1.0
    assert score.grounded_number_precision == 1.0
    assert score.changed is True
    assert score.forbidden_hits == ()


def test_rewrite_scorer_finds_fact_loss_and_invention() -> None:
    case = REWRITE_CASES[0]
    score = score_rewrite(
        case["current_content"],
        case["user_request"],
        "作为架构师带领10人团队，使营收提升 80%。",
        required_fragments=case["required_fragments"],
        forbidden_fragments=case["forbidden_fragments"],
    )

    assert score.fact_recall == 0.0
    assert "80%" in score.invented_numbers
    assert {"10人团队", "营收", "架构师"} <= set(score.forbidden_hits)


def test_quality_golden_suite_has_stable_unique_case_ids() -> None:
    retrieval_names = [case["name"] for case in RETRIEVAL_CASES]
    import_names = [case["name"] for case in IMPORT_CASES]
    jd_import_names = [case["name"] for case in JD_IMPORT_CASES]
    rewrite_names = [case["name"] for case in REWRITE_CASES]
    evidence_ids = [
        evidence["evidence_id"]
        for experience in RETRIEVAL_EXPERIENCES
        for evidence in experience["evidence"]
    ]

    assert len(RETRIEVAL_CASES) >= 4
    assert len(IMPORT_CASES) >= 2
    assert len(JD_IMPORT_CASES) >= 2
    assert len(REWRITE_CASES) >= 2
    assert len(retrieval_names) == len(set(retrieval_names))
    assert len(import_names) == len(set(import_names))
    assert len(jd_import_names) == len(set(jd_import_names))
    assert len(rewrite_names) == len(set(rewrite_names))
    assert len(evidence_ids) == len(set(evidence_ids))
