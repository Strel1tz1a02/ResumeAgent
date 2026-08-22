"""Evidence 召回评分器、黄金集契约与生产检索质量评测。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from statistics import mean
from typing import Any

import pytest
from langchain_qdrant import FastEmbedSparse, QdrantVectorStore, RetrievalMode
from qdrant_client import QdrantClient, models

from app.config import settings
from app.resume_generation.indexing import QdrantEvidenceIndexer
from app.resume_generation.retriever import (
    FastEmbedDenseEmbeddings,
    QdrantEvidenceRetriever,
    QdrantEvidenceStore,
    build_documents,
)
from app.resume_generation.schemas import ExperienceSnapshot, SearchTask
from tests.evals.golden.retrieval_cases import (
    RETRIEVAL_CASES,
    RETRIEVAL_CUTOFFS,
    RETRIEVAL_DATASET_VERSION,
    RETRIEVAL_EXPERIENCES,
    RETRIEVAL_RANKING_K,
    RETRIEVAL_RECALL_K,
)
from tests.evals.quality_report import write_quality_report
from tests.evals.quality_scorers import score_retrieval

_SEARCH_INTENTS = {
    "exact_skill",
    "responsibility",
    "scenario",
    "result_evidence",
    "transferable",
}


def test_retrieval_scorer_rewards_early_relevant_result() -> None:
    score = score_retrieval([105, 101, 106], {101}, k=3)

    assert score.hit_at_k is True
    assert score.precision_at_k == pytest.approx(1 / 3)
    assert score.recall_at_k == 1.0
    assert score.reciprocal_rank == 0.5
    assert score.average_precision_at_k == 0.5
    assert 0.6 < score.ndcg_at_k < 0.7


def test_retrieval_scorer_measures_partial_multi_relevant_recall() -> None:
    score = score_retrieval([900, 101, 901, 102], {101, 102, 103}, k=4)

    assert score.hit_at_k is True
    assert score.precision_at_k == 0.5
    assert score.recall_at_k == pytest.approx(2 / 3)
    assert score.reciprocal_rank == 0.5
    assert score.average_precision_at_k == pytest.approx(1 / 3)
    assert score.ndcg_at_k == pytest.approx(0.4981892575)


def test_retrieval_scorer_deduplicates_ranked_ids() -> None:
    score = score_retrieval([101, 101, 102, 900], {101, 102}, k=3)

    assert score.precision_at_k == pytest.approx(2 / 3)
    assert score.recall_at_k == 1.0
    assert score.average_precision_at_k == 1.0
    assert score.ndcg_at_k == 1.0


def test_retrieval_scorer_rejects_missed_result() -> None:
    score = score_retrieval([105, 106, 103], {101}, k=3)

    assert asdict(score) == {
        "precision_at_k": 0.0,
        "recall_at_k": 0.0,
        "reciprocal_rank": 0.0,
        "average_precision_at_k": 0.0,
        "ndcg_at_k": 0.0,
        "hit_at_k": False,
    }


@pytest.mark.parametrize(
    ("relevant_ids", "k", "message"),
    [(set(), 3, "relevant_ids"), ({101}, 0, "k must be positive")],
)
def test_retrieval_scorer_rejects_invalid_input(
    relevant_ids: set[int], k: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        score_retrieval([101], relevant_ids, k=k)


def test_retrieval_golden_cases_have_auditable_hard_negatives() -> None:
    """防止黄金集退回到少量、单正例、词面直抄的烟测。"""
    experiences = [
        ExperienceSnapshot.model_validate(item) for item in RETRIEVAL_EXPERIENCES
    ]
    documents = build_documents(experiences)
    names = [case["name"] for case in RETRIEVAL_CASES]
    queries = [case["query"].strip().casefold() for case in RETRIEVAL_CASES]
    evidence_ids = [document.evidence_id for document in documents]
    all_tags = {tag for case in RETRIEVAL_CASES for tag in case["tags"]}
    oracle_ids = {
        evidence_id
        for case in RETRIEVAL_CASES
        for evidence_id in case["relevant_evidence_ids"]
    }

    assert RETRIEVAL_DATASET_VERSION
    assert RETRIEVAL_CUTOFFS == tuple(sorted(set(RETRIEVAL_CUTOFFS)))
    assert RETRIEVAL_RANKING_K in RETRIEVAL_CUTOFFS
    assert RETRIEVAL_RECALL_K in RETRIEVAL_CUTOFFS
    assert max(RETRIEVAL_CUTOFFS) <= len(evidence_ids)
    assert RETRIEVAL_RECALL_K / len(evidence_ids) <= 0.25
    assert len(RETRIEVAL_CASES) >= 12
    assert len(evidence_ids) >= 20
    assert len(names) == len(set(names))
    assert len(queries) == len(set(queries))
    assert len(evidence_ids) == len(set(evidence_ids))
    assert oracle_ids <= set(evidence_ids)
    assert {case["intent"] for case in RETRIEVAL_CASES} == _SEARCH_INTENTS
    assert {
        "lexical",
        "semantic",
        "mixed_language",
        "hard_negative",
        "same_parent_negative",
        "multi_relevant",
    } <= all_tags
    assert sum(len(case["relevant_evidence_ids"]) > 1 for case in RETRIEVAL_CASES) >= 3

    for case in RETRIEVAL_CASES:
        relevant_ids = case["relevant_evidence_ids"]
        assert case["name"].strip()
        assert case["query"].strip()
        assert case["tags"]
        assert len(relevant_ids) == len(set(relevant_ids))
        assert 1 <= len(relevant_ids) <= RETRIEVAL_RECALL_K
        assert set(case["oracle_reasons"]) == set(relevant_ids)
        assert all(reason.strip() for reason in case["oracle_reasons"].values())


def _aggregate_at_k(case_reports: list[dict[str, Any]], k: int) -> dict[str, float]:
    """聚合指定 cutoff 的宏指标、微召回和最差案例召回。"""
    metrics = [report["metrics_by_k"][str(k)] for report in case_reports]
    total_relevant = sum(len(report["oracle_ids"]) for report in case_reports)
    total_hits = sum(
        len(set(report["ranked_ids"][:k]) & set(report["oracle_ids"]))
        for report in case_reports
    )
    return {
        "hit_rate": mean(float(item["hit_at_k"]) for item in metrics),
        "macro_precision": mean(item["precision_at_k"] for item in metrics),
        "macro_recall": mean(item["recall_at_k"] for item in metrics),
        "micro_recall": total_hits / total_relevant,
        "mean_reciprocal_rank": mean(item["reciprocal_rank"] for item in metrics),
        "mean_average_precision": mean(
            item["average_precision_at_k"] for item in metrics
        ),
        "mean_ndcg": mean(item["ndcg_at_k"] for item in metrics),
        "worst_case_recall": min(item["recall_at_k"] for item in metrics),
    }


def _slice_metrics(
    case_reports: list[dict[str, Any]], *, field: str, k: int
) -> dict[str, dict[str, float]]:
    """按 intent 或标签输出切片，便于定位某类查询的退化。"""
    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for report in case_reports:
        labels = report[field] if isinstance(report[field], list) else [report[field]]
        for label in labels:
            groups[label].append(report)
    return {
        label: {"case_count": len(reports), **_aggregate_at_k(reports, k)}
        for label, reports in sorted(groups.items())
    }


@pytest.mark.eval
async def test_retrieval_quality_meets_golden_thresholds() -> None:
    """使用生产 dense+sparse 模型、索引器和 Qdrant RRF 评估检索质量。"""
    ranking_k = RETRIEVAL_RANKING_K
    recall_k = RETRIEVAL_RECALL_K
    query_k = max(RETRIEVAL_CUTOFFS)
    thresholds = {
        f"minimum_hit_rate_at_{ranking_k}": 1.0,
        f"minimum_mrr_at_{ranking_k}": 0.80,
        f"minimum_ndcg_at_{ranking_k}": 0.85,
        f"minimum_macro_recall_at_{recall_k}": 0.95,
        f"minimum_micro_recall_at_{recall_k}": 0.90,
        f"minimum_map_at_{recall_k}": 0.85,
        f"minimum_worst_case_recall_at_{recall_k}": 0.50,
        f"minimum_multi_relevant_macro_recall_at_{recall_k}": 0.80,
    }
    model_metadata = {
        "dense": settings.qdrant_dense_model,
        "sparse": settings.qdrant_sparse_model,
    }
    case_reports: list[dict[str, Any]] = []
    client: QdrantClient | None = None
    try:
        dense = FastEmbedDenseEmbeddings(settings.qdrant_dense_model)
        sparse = FastEmbedSparse(model_name=settings.qdrant_sparse_model)
        dimension = len(dense.embed_query("召回质量评测"))
        client = QdrantClient(":memory:")
        collection = "resume_evidence_quality_eval"
        client.create_collection(
            collection_name=collection,
            vectors_config={
                "dense": models.VectorParams(
                    size=dimension,
                    distance=models.Distance.COSINE,
                )
            },
            sparse_vectors_config={
                "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
        vector_store = QdrantVectorStore(
            client=client,
            collection_name=collection,
            embedding=dense,
            sparse_embedding=sparse,
            retrieval_mode=RetrievalMode.HYBRID,
            vector_name="dense",
            sparse_vector_name="sparse",
        )
        backend = QdrantEvidenceStore(
            collection_name=collection,
            dense_model=settings.qdrant_dense_model,
            sparse_model=settings.qdrant_sparse_model,
            vector_store=vector_store,
        )
        retriever = QdrantEvidenceRetriever(backend=backend)
        indexer = QdrantEvidenceIndexer(backend)
        experiences = [
            ExperienceSnapshot.model_validate(item) for item in RETRIEVAL_EXPERIENCES
        ]
        for experience in experiences:
            await indexer.sync(experience.experience_id, experience)
        documents = build_documents(experiences)

        for case in RETRIEVAL_CASES:
            task = SearchTask(
                task_id=case["name"],
                coverage_item_ids=[case["name"]],
                intent=case["intent"],
                query=case["query"],
                top_k=query_k,
            )
            found = await retriever.retrieve([task], documents)
            ranked_ids = [item.document.evidence_id for item in found]
            relevant_ids = set(case["relevant_evidence_ids"])
            scores = {
                str(k): asdict(score_retrieval(ranked_ids, relevant_ids, k=k))
                for k in RETRIEVAL_CUTOFFS
            }
            ranks = {
                evidence_id: ranked_ids.index(evidence_id) + 1
                for evidence_id in relevant_ids
                if evidence_id in ranked_ids
            }
            case_reports.append(
                {
                    "name": case["name"],
                    "intent": case["intent"],
                    "tags": case["tags"],
                    "query": case["query"],
                    "oracle_ids": case["relevant_evidence_ids"],
                    "oracle_reasons": case["oracle_reasons"],
                    "ranked_ids": ranked_ids,
                    "ranked_results": [
                        {
                            "evidence_id": item.document.evidence_id,
                            "score": item.retrieval_score,
                        }
                        for item in found
                    ],
                    "relevant_ranks": ranks,
                    "missing_relevant_at_recall_k": sorted(
                        relevant_ids - set(ranked_ids[:recall_k])
                    ),
                    "metrics_by_k": scores,
                }
            )
    except Exception as error:  # noqa: BLE001 - 报告必须保留初始化/下载故障
        path = write_quality_report(
            "retrieval",
            model=model_metadata,
            thresholds=thresholds,
            cases=case_reports + [{"error": f"{type(error).__name__}: {error}"}],
            summary={
                "passed": False,
                "dataset_version": RETRIEVAL_DATASET_VERSION,
                "completed_cases": len(case_reports),
                "total_cases": len(RETRIEVAL_CASES),
            },
        )
        pytest.fail(f"召回质量评测无法完成；报告：{path}\n{error}")
    finally:
        if client is not None:
            client.close()

    metrics_by_k = {str(k): _aggregate_at_k(case_reports, k) for k in RETRIEVAL_CUTOFFS}
    ranking_metrics = metrics_by_k[str(ranking_k)]
    recall_metrics = metrics_by_k[str(recall_k)]
    multi_relevant_reports = [
        report for report in case_reports if "multi_relevant" in report["tags"]
    ]
    multi_relevant_metrics = _aggregate_at_k(multi_relevant_reports, recall_k)
    checks = {
        f"hit_rate_at_{ranking_k}": ranking_metrics["hit_rate"]
        >= thresholds[f"minimum_hit_rate_at_{ranking_k}"],
        f"mrr_at_{ranking_k}": ranking_metrics["mean_reciprocal_rank"]
        >= thresholds[f"minimum_mrr_at_{ranking_k}"],
        f"ndcg_at_{ranking_k}": ranking_metrics["mean_ndcg"]
        >= thresholds[f"minimum_ndcg_at_{ranking_k}"],
        f"macro_recall_at_{recall_k}": recall_metrics["macro_recall"]
        >= thresholds[f"minimum_macro_recall_at_{recall_k}"],
        f"micro_recall_at_{recall_k}": recall_metrics["micro_recall"]
        >= thresholds[f"minimum_micro_recall_at_{recall_k}"],
        f"map_at_{recall_k}": recall_metrics["mean_average_precision"]
        >= thresholds[f"minimum_map_at_{recall_k}"],
        f"worst_case_recall_at_{recall_k}": recall_metrics["worst_case_recall"]
        >= thresholds[f"minimum_worst_case_recall_at_{recall_k}"],
        f"multi_relevant_macro_recall_at_{recall_k}": multi_relevant_metrics[
            "macro_recall"
        ]
        >= thresholds[f"minimum_multi_relevant_macro_recall_at_{recall_k}"],
    }
    summary = {
        "passed": all(checks.values()),
        "dataset_version": RETRIEVAL_DATASET_VERSION,
        "corpus_evidence_count": len(
            {
                evidence["evidence_id"]
                for experience in RETRIEVAL_EXPERIENCES
                for evidence in experience["evidence"]
            }
        ),
        "case_count": len(case_reports),
        "ranking_k": ranking_k,
        "recall_k": recall_k,
        "checks": checks,
        "metrics_by_k": metrics_by_k,
        "multi_relevant_metrics_at_recall_k": multi_relevant_metrics,
        "intent_slices_at_recall_k": _slice_metrics(
            case_reports, field="intent", k=recall_k
        ),
        "tag_slices_at_recall_k": _slice_metrics(
            case_reports, field="tags", k=recall_k
        ),
    }
    path = write_quality_report(
        "retrieval",
        model=model_metadata,
        thresholds=thresholds,
        cases=case_reports,
        summary=summary,
    )
    failed_checks = [name for name, passed in checks.items() if not passed]
    assert not failed_checks, f"未通过：{', '.join(failed_checks)}；报告：{path}"
