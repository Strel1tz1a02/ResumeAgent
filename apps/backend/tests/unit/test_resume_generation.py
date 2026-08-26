"""简历生成检索、组合和 Graph 停止条件。"""

import json
import logging
from typing import Any

import pytest
from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore, RetrievalMode, SparseEmbeddings
from qdrant_client import QdrantClient, models

from app.resume_generation.graph import (
    ResumeGenerationGraphDependencies,
    build_resume_generation_graph,
)
from app.resume_generation.indexing import QdrantEvidenceIndexer
from app.resume_generation.model import (
    FallbackResumeGenerationModel,
    LangChainResumeGenerationModel,
    RuleBasedResumeGenerationModel,
)
from app.resume_generation.planner import assemble_plan, materialize_resume
from app.resume_generation.retriever import (
    QdrantEvidenceRetriever,
    QdrantEvidenceStore,
    _to_langchain_document,
    build_documents,
)
from app.resume_generation.schemas import (
    CoverageItem,
    DraftBullet,
    DraftClaimAssessment,
    DraftedExperience,
    DraftFactCheckResult,
    EvidenceJudgment,
    EvidenceSnapshot,
    ExperienceSnapshot,
    JDAnalysisSnapshot,
    JDAnalysisSourceSnapshot,
    JDRequirementSnapshot,
    PlannedExperience,
    ResumeConstraints,
    ResumeDraft,
    ResumePlan,
    RetrievedEvidence,
    SearchTask,
)
from app.resume_generation.validation import validate_draft_references


class _FakeQdrantVectorStore:
    """只模拟 LangChain VectorStore 边界，不在测试中复制检索算法。"""

    def __init__(self) -> None:
        self.documents: dict[int, Any] = {}
        self.search_calls: list[dict[str, Any]] = []

    def add_documents(self, documents: list[Any], *, ids: list[int]) -> list[int]:
        self.documents.update(zip(ids, documents, strict=True))
        return ids

    def similarity_search_with_score(
        self,
        query: str,
        k: int,
        **kwargs: Any,
    ) -> list[tuple[Any, float]]:
        self.search_calls.append({"query": query, "k": k, **kwargs})
        return [
            (document, max(0.1, 0.9 - index * 0.05))
            for index, document in enumerate(self.documents.values())
        ][:k]


class _PerQueryScoreVectorStore(_FakeQdrantVectorStore):
    """为同一片段按不同查询返回不同融合分，验证合并前日志。"""

    def similarity_search_with_score(
        self,
        query: str,
        k: int,
        **kwargs: Any,
    ) -> list[tuple[Any, float]]:
        self.search_calls.append({"query": query, "k": k, **kwargs})
        if query == "无命中证据":
            return []
        score = 0.91 if query == "FastAPI API" else 0.37
        return [(document, score) for document in self.documents.values()][:k]


def _qdrant_retriever(
    experiences: list[ExperienceSnapshot] | None = None,
) -> QdrantEvidenceRetriever:
    store = _FakeQdrantVectorStore()
    documents = build_documents(experiences or [])
    store.add_documents(
        [_to_langchain_document(item) for item in documents],
        ids=[item.evidence_id for item in documents],
    )
    return QdrantEvidenceRetriever(vector_store=store)


class _DenseEmbeddings(Embeddings):
    """为本地 Qdrant 契约测试提供固定维度向量。"""

    @staticmethod
    def _embed(text: str) -> list[float]:
        return [1.0, 0.0] if "FastAPI" in text else [0.0, 1.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class _SparseEmbeddings(SparseEmbeddings):
    """为本地 Qdrant 契约测试提供固定稀疏向量。"""

    @staticmethod
    def _embed(text: str) -> models.SparseVector:
        index = 1 if "FastAPI" in text else 2
        return models.SparseVector(indices=[index], values=[1.0])

    def embed_documents(self, texts: list[str]) -> list[models.SparseVector]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> models.SparseVector:
        return self._embed(text)


def _source() -> JDAnalysisSourceSnapshot:
    return JDAnalysisSourceSnapshot(
        id=1,
        company="Example",
        job_name="Backend Engineer",
        type="backend",
        location="Shanghai",
        status="confirmed",
        revision=0,
        requirements=[
            JDRequirementSnapshot(
                id=1,
                priority="required",
                content="使用 Python 和 FastAPI 设计 API",
                sort_order=0,
                revision=0,
            ),
            JDRequirementSnapshot(
                id=2,
                priority="preferred",
                content="OpenSearch 检索",
                sort_order=1,
                revision=0,
            ),
        ],
    )


async def test_llm_jd_analysis_normalizes_localized_importance() -> None:
    calls: list[dict[str, Any]] = []

    async def completion(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, "kwargs": kwargs})
        return {
            "coverage_items": [
                {
                    "coverage_id": "python-api",
                    "source_requirement_ids": [1],
                    "statement": "使用 Python 和 FastAPI 设计 API",
                    "importance": "高",
                    "capability": "Python API 设计",
                    "evidence_expectation": ["实际项目"],
                    "aliases": ["Python", "FastAPI"],
                },
                {
                    "coverage_id": "opensearch",
                    "source_requirement_ids": [2],
                    "statement": "OpenSearch 检索",
                    "importance": "中",
                    "capability": "检索系统开发",
                    "evidence_expectation": [],
                    "aliases": ["OpenSearch"],
                },
            ]
        }

    result = await LangChainResumeGenerationModel(completion).analyze_jd(_source())

    assert [item.importance for item in result] == ["must", "should"]
    assert len(calls) == 1
    system_prompt = calls[0]["kwargs"]["system_prompt"]
    input_prompt = calls[0]["args"][0]
    assert "EXPECTED_OUTPUT_SCHEMA" in system_prompt
    assert '"statement"' in system_prompt
    assert '"capability"' in system_prompt
    assert '"required"' in system_prompt
    assert "UNTRUSTED_DOMAIN_DATA name=resume_generation_input" in input_prompt
    assert calls[0]["kwargs"]["retries"] == 1


async def test_llm_structure_repair_keeps_schema_and_requests_complete_output() -> None:
    calls: list[dict[str, Any]] = []

    async def completion(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, "kwargs": kwargs})
        if len(calls) == 1:
            return {
                "coverage_items": [
                    {
                        "coverage_id": "python-api",
                        "source_requirement_ids": [1],
                        "importance": "must",
                    }
                ]
            }
        return {
            "coverage_items": [
                {
                    "coverage_id": "python-api",
                    "source_requirement_ids": [1],
                    "statement": "使用 Python 和 FastAPI 设计 API",
                    "importance": "must",
                    "capability": "Python API 设计",
                    "evidence_expectation": [],
                    "aliases": ["Python", "FastAPI"],
                },
                {
                    "coverage_id": "opensearch",
                    "source_requirement_ids": [2],
                    "statement": "OpenSearch 检索",
                    "importance": "should",
                    "capability": "检索系统开发",
                    "evidence_expectation": [],
                    "aliases": ["OpenSearch"],
                },
            ]
        }

    result = await LangChainResumeGenerationModel(completion).analyze_jd(_source())

    assert len(result) == 2
    assert len(calls) == 2
    assert calls[0]["kwargs"]["system_prompt"] == calls[1]["kwargs"]["system_prompt"]
    assert calls[1]["kwargs"]["retries"] == 1
    repair_prompt = calls[1]["args"][0]
    assert "VALIDATION_ERRORS" in repair_prompt
    assert "重新输出完整 JSON 对象" in repair_prompt
    assert "Field required" in repair_prompt


async def test_llm_judge_filters_skills_outside_candidate_allowlist() -> None:
    calls: list[dict[str, Any]] = []

    async def completion(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, "kwargs": kwargs})
        return {
            "judgments": [
                {
                    "evidence_id": 11,
                    "experience_id": 1,
                    "coverage_item_ids": ["python-api"],
                    "relevance": 0.9,
                    "evidence_strength": 0.8,
                    "uniqueness": 0.6,
                    "supported_skills": ["fastapi", "Web 框架开发"],
                    "unsupported_risk": [],
                    "reason": "存在直接项目证据",
                }
            ]
        }

    analysis = JDAnalysisSnapshot(
        source=_source(),
        target_title="Backend Engineer",
        coverage_items=[
            CoverageItem(
                coverage_id="python-api",
                source_requirement_ids=[1],
                statement="使用 Python 和 FastAPI 设计 API",
                importance="must",
                capability="Python API 设计",
                aliases=["Python", "FastAPI"],
            )
        ],
    )
    task = SearchTask(
        task_id="t1",
        coverage_item_ids=["python-api"],
        intent="exact_skill",
        query="Python FastAPI",
    )
    document = build_documents(
        [
            _experience(
                1,
                11,
                action="设计接口参数校验",
                result="服务稳定运行",
                technologies=["FastAPI"],
            )
        ]
    )[0]
    candidate = RetrievedEvidence(
        document=document,
        retrieval_score=0.9,
        task_ids=["t1"],
    )

    result = await LangChainResumeGenerationModel(completion).judge(
        analysis, [task], [candidate]
    )

    assert result[0].supported_skills == ["FastAPI"]
    assert '"allowed_supported_skills": ["FastAPI"]' in calls[0]["args"][0]


def _experience(
    experience_id: int,
    evidence_id: int,
    *,
    action: str,
    result: str | None,
    technologies: list[str],
) -> ExperienceSnapshot:
    return ExperienceSnapshot(
        experience_id=experience_id,
        kind="project",
        title=f"Project {experience_id}",
        organization=None,
        role="Developer",
        start_date="2025-01",
        end_date="2025-06",
        background="知识检索项目",
        technologies=technologies,
        tags=[],
        completeness=90,
        updated_at="2026-01-01T00:00:00+00:00",
        evidence=[
            EvidenceSnapshot(
                evidence_id=evidence_id,
                action=action,
                result=result,
                updated_at="2026-01-01T00:00:00+00:00",
            )
        ],
    )


async def test_llm_fact_check_receives_claims_with_only_bound_evidence() -> None:
    calls: list[dict[str, Any]] = []

    async def completion(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, "kwargs": kwargs})
        return {
            "assessments": [
                {
                    "claim_id": "summary",
                    "verdict": "supported",
                    "unsupported_fragments": [],
                    "reason": "Summary 由引用 Evidence 支持",
                },
                {
                    "claim_id": "experience:1:bullet:0",
                    "verdict": "unsupported",
                    "unsupported_fragments": ["主导 Kubernetes 架构"],
                    "reason": "引用原文没有 Kubernetes 或主导职责",
                },
            ]
        }

    experiences = [
        _experience(
            1,
            11,
            action="使用 FastAPI 开发接口",
            result="完成服务交付",
            technologies=["FastAPI"],
        )
    ]
    experiences[0].evidence.append(
        EvidenceSnapshot(
            evidence_id=12,
            action="UNBOUND_EVIDENCE_MARKER",
            result="未被 Draft 引用",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )
    plan = ResumePlan(
        selected_experiences=[
            PlannedExperience(
                experience_id=1,
                section="personalProjects",
                evidence_ids=[11],
                bullet_budget=1,
            )
        ],
        search_rounds=1,
        coverage_ratio=1,
    )
    draft = ResumeDraft(
        summary="具备 FastAPI 项目经验",
        summary_evidence_ids=[11],
        experiences=[
            DraftedExperience(
                experience_id=1,
                bullets=[
                    DraftBullet(
                        experience_id=1,
                        evidence_ids=[11],
                        text="使用 FastAPI 开发接口并主导 Kubernetes 架构",
                    )
                ],
            )
        ],
    )

    result = await LangChainResumeGenerationModel(completion).validate_draft(
        draft, plan, experiences
    )

    assert result.model_used is True
    assert [item.verdict for item in result.assessments] == [
        "supported",
        "unsupported",
    ]
    prompt = calls[0]["args"][0]
    assert "experience:1:bullet:0" in prompt
    assert '"evidence_id": 11' in prompt
    assert "使用 FastAPI 开发接口" in prompt
    assert "UNBOUND_EVIDENCE_MARKER" not in prompt
    assert "知识检索项目" not in prompt
    assert '"technologies"' not in prompt


def test_supported_fact_assessment_cannot_report_unsupported_fragments() -> None:
    """完全支持与“不受支持片段”是互斥状态，避免模型输出自相矛盾。"""
    with pytest.raises(ValueError, match="supported assessment"):
        DraftClaimAssessment(
            claim_id="summary",
            verdict="supported",
            unsupported_fragments=["并不存在的片段"],
        )


async def test_llm_fact_check_requires_exact_claim_set() -> None:
    async def completion(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"assessments": []}

    experiences = [
        _experience(
            1,
            11,
            action="使用 FastAPI 开发接口",
            result="完成服务交付",
            technologies=["FastAPI"],
        )
    ]
    plan = ResumePlan(
        selected_experiences=[
            PlannedExperience(
                experience_id=1,
                section="personalProjects",
                evidence_ids=[11],
                bullet_budget=1,
            )
        ],
        search_rounds=1,
        coverage_ratio=1,
    )
    draft = ResumeDraft(
        experiences=[
            DraftedExperience(
                experience_id=1,
                bullets=[
                    DraftBullet(
                        experience_id=1,
                        evidence_ids=[11],
                        text="使用 FastAPI 开发接口",
                    )
                ],
            )
        ]
    )

    with pytest.raises(ValueError, match="assess every claim exactly once"):
        await LangChainResumeGenerationModel(completion).validate_draft(
            draft, plan, experiences
        )


async def test_qdrant_retriever_uses_parent_metadata_and_returns_evidence_chunk() -> (
    None
):
    experience = _experience(
        1,
        11,
        action="设计接口参数校验",
        result="服务稳定运行",
        technologies=["FastAPI"],
    )
    retriever = _qdrant_retriever()
    documents = build_documents([experience])
    retriever._store.add_documents(
        [_to_langchain_document(item) for item in documents],
        ids=[item.evidence_id for item in documents],
    )
    results = await retriever.retrieve(
        [
            SearchTask(
                task_id="t1",
                coverage_item_ids=["c1"],
                intent="exact_skill",
                query="FastAPI API",
                top_k=5,
            )
        ],
        documents,
    )

    assert [item.document.evidence_id for item in results] == [11]
    assert results[0].document.experience_id == 1
    assert results[0].matched_terms == []
    assert retriever._store.search_calls[0]["hybrid_fusion"].fusion.value == "rrf"


async def test_qdrant_retriever_executes_native_dense_sparse_rrf_query() -> None:
    client = QdrantClient(":memory:")
    client.create_collection(
        collection_name="resume_evidence_test",
        vectors_config={
            "dense": models.VectorParams(size=2, distance=models.Distance.COSINE)
        },
        sparse_vectors_config={
            "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)
        },
    )
    store = QdrantVectorStore(
        client=client,
        collection_name="resume_evidence_test",
        embedding=_DenseEmbeddings(),
        sparse_embedding=_SparseEmbeddings(),
        retrieval_mode=RetrievalMode.HYBRID,
        vector_name="dense",
        sparse_vector_name="sparse",
    )
    backend = QdrantEvidenceStore(
        collection_name="resume_evidence_test", vector_store=store
    )
    retriever = QdrantEvidenceRetriever(backend=backend)
    documents = build_documents(
        [
            _experience(
                1,
                11,
                action="使用 FastAPI 设计接口",
                result="完成交付",
                technologies=["FastAPI"],
            ),
            _experience(
                2,
                22,
                action="使用 OpenSearch 实现检索",
                result="完成上线",
                technologies=["OpenSearch"],
            ),
        ]
    )

    await QdrantEvidenceIndexer(backend).sync(1, _experience(
        1,
        11,
        action="使用 FastAPI 设计接口",
        result="完成交付",
        technologies=["FastAPI"],
    ))
    await QdrantEvidenceIndexer(backend).sync(2, _experience(
        2,
        22,
        action="使用 OpenSearch 实现检索",
        result="完成上线",
        technologies=["OpenSearch"],
    ))
    results = await retriever.retrieve(
        [
            SearchTask(
                task_id="fastapi",
                coverage_item_ids=["api"],
                intent="exact_skill",
                query="FastAPI API",
                top_k=2,
            )
        ],
        documents,
    )

    assert results[0].document.evidence_id == 11


async def test_retrieval_logs_each_query_hit_before_score_merge(caplog) -> None:
    experience = _experience(
        1,
        11,
        action="使用 FastAPI 设计接口",
        result="完成服务交付",
        technologies=["FastAPI"],
    )
    documents = build_documents([experience])
    store = _PerQueryScoreVectorStore()
    store.add_documents(
        [_to_langchain_document(item) for item in documents],
        ids=[item.evidence_id for item in documents],
    )
    retriever = QdrantEvidenceRetriever(vector_store=store)
    caplog.set_level(
        logging.INFO,
        logger="app.resume_generation.observability",
    )

    results = await retriever.retrieve_with_trace(
        [
            SearchTask(
                task_id="fastapi",
                coverage_item_ids=["api"],
                intent="exact_skill",
                query="FastAPI API",
                top_k=2,
            ),
            SearchTask(
                task_id="delivery",
                coverage_item_ids=["delivery"],
                intent="result_evidence",
                query="服务交付",
                top_k=2,
            ),
            SearchTask(
                task_id="missing",
                coverage_item_ids=["missing"],
                intent="scenario",
                query="无命中证据",
                top_k=2,
            ),
        ],
        documents,
        run_id="run-log-1",
        search_round=2,
    )

    events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "app.resume_generation.observability"
        and json.loads(record.getMessage()).get("event")
        == "resume_generation.retrieval"
    ]
    assert [event["question"]["query"] for event in events] == [
        "FastAPI API",
        "服务交付",
        "无命中证据",
    ]
    assert [
        event["hits"][0]["qdrant_fused_score_raw"]
        for event in events
        if event["hits"]
    ] == [0.91, 0.37]
    assert all(event["run_id"] == "run-log-1" for event in events)
    assert all(event["search_round"] == 2 for event in events)
    assert all(event["schema_version"] == 1 for event in events)
    assert all(event["retrieval_mode"] == "hybrid" for event in events)
    assert all(event["fusion"] == "rrf" for event in events)
    assert events[0]["hits"][0]["evidence_id"] == 11
    assert events[0]["hits"][0]["searchable_text"] == documents[0].searchable_text()
    assert "使用 FastAPI 设计接口" in events[0]["hits"][0]["searchable_text"]
    assert events[2]["hit_count"] == 0
    assert events[2]["hits"] == []
    assert results[0].retrieval_score == 0.91

    caplog.clear()
    await retriever.retrieve(
        [
            SearchTask(
                task_id="unscoped",
                coverage_item_ids=["api"],
                intent="exact_skill",
                query="FastAPI API",
                top_k=1,
            )
        ],
        documents,
    )
    assert not [
        record
        for record in caplog.records
        if record.name == "app.resume_generation.observability"
    ]


def test_portfolio_promotes_unique_omitted_evidence_to_skill() -> None:
    source = _source()
    analysis = JDAnalysisSnapshot(
        source=source,
        target_title=source.job_name,
        coverage_items=[
            CoverageItem(
                coverage_id="python",
                source_requirement_ids=[1],
                statement="Python FastAPI",
                importance="must",
                capability="Python FastAPI",
                aliases=["Python", "FastAPI"],
            ),
            CoverageItem(
                coverage_id="opensearch",
                source_requirement_ids=[2],
                statement="OpenSearch 检索",
                importance="should",
                capability="OpenSearch",
                aliases=["OpenSearch"],
            ),
        ],
    )
    experiences = [
        _experience(
            1,
            11,
            action="使用 Python FastAPI 开发接口",
            result="完成交付",
            technologies=["Python", "FastAPI"],
        ),
        _experience(
            2,
            22,
            action="使用 OpenSearch 实现检索",
            result="完成联合调试",
            technologies=["OpenSearch"],
        ),
    ]
    judgments = [
        EvidenceJudgment(
            evidence_id=11,
            experience_id=1,
            coverage_item_ids=["python"],
            relevance=0.95,
            evidence_strength=0.9,
            uniqueness=0.5,
            supported_skills=["Python", "FastAPI"],
        ),
        EvidenceJudgment(
            evidence_id=22,
            experience_id=2,
            coverage_item_ids=["opensearch"],
            relevance=0.8,
            evidence_strength=0.7,
            uniqueness=0.9,
            supported_skills=["OpenSearch"],
        ),
    ]

    plan = assemble_plan(
        analysis,
        experiences,
        judgments,
        ResumeConstraints(max_project_experiences=1),
        search_rounds=1,
    )

    assert [item.experience_id for item in plan.selected_experiences] == [1]
    assert [(item.skill, item.evidence_ids) for item in plan.promoted_skills] == [
        ("OpenSearch", [22])
    ]
    assert plan.coverage_ratio == 1.0

    resume_data, provenance = materialize_resume(
        analysis,
        plan,
        ResumeDraft(
            experiences=[
                DraftedExperience(
                    experience_id=1,
                    bullets=[
                        DraftBullet(
                            experience_id=1,
                            evidence_ids=[11],
                            text="使用 Python FastAPI 开发接口",
                        )
                    ],
                )
            ]
        ),
        experiences,
    )
    assert resume_data.additional.technicalSkills == [
        "Python",
        "FastAPI",
        "OpenSearch",
    ]
    assert [item.model_dump(mode="json") for item in provenance.skills] == [
        {"skill": "Python", "evidence_ids": [11]},
        {"skill": "FastAPI", "evidence_ids": [11]},
        {"skill": "OpenSearch", "evidence_ids": [22]},
    ]


def test_portfolio_prefers_unique_support_over_risk_free_hard_negative() -> None:
    source = _source()
    analysis = JDAnalysisSnapshot(
        source=source,
        target_title=source.job_name,
        coverage_items=[
            CoverageItem(
                coverage_id="hybrid-retrieval",
                source_requirement_ids=[1],
                statement="混合检索",
                importance="must",
                capability="混合检索",
            ),
            CoverageItem(
                coverage_id="offline-evaluation",
                source_requirement_ids=[2],
                statement="离线评估",
                importance="must",
                capability="离线评估",
            ),
        ],
    )
    experiences = [
        _experience(
            1,
            11,
            action="建设混合检索与离线评估",
            result="Recall@10 提升",
            technologies=["Qdrant", "BM25"],
        ),
        _experience(
            2,
            22,
            action="使用 Embedding 训练分类器",
            result="准确率达到 91%",
            technologies=["Embedding"],
        ),
        _experience(
            3,
            33,
            action="建立可重复的离线评估流程",
            result="减少人工统计误差",
            technologies=["Python"],
        ),
    ]
    judgments = [
        EvidenceJudgment(
            evidence_id=11,
            experience_id=1,
            coverage_item_ids=["hybrid-retrieval", "offline-evaluation"],
            relevance=0.75,
            evidence_strength=0.9,
            uniqueness=1.0,
            supported_skills=["Qdrant", "BM25"],
            unsupported_risk=[
                "Qdrant 使用细节不够完整",
                "BM25 使用细节不够完整",
                "Embedding 使用细节不够完整",
            ],
        ),
        EvidenceJudgment(
            evidence_id=22,
            experience_id=2,
            coverage_item_ids=["hybrid-retrieval"],
            relevance=1.0,
            evidence_strength=0.9,
            uniqueness=0.5,
            supported_skills=["Embedding"],
        ),
        EvidenceJudgment(
            evidence_id=33,
            experience_id=3,
            coverage_item_ids=["offline-evaluation"],
            relevance=0.75,
            evidence_strength=0.7,
            uniqueness=1.0,
            supported_skills=["Python"],
            unsupported_risk=["未明确使用指定离线指标"],
        ),
    ]

    plan = assemble_plan(
        analysis,
        experiences,
        judgments,
        ResumeConstraints(max_project_experiences=2),
        search_rounds=1,
    )

    assert [item.experience_id for item in plan.selected_experiences] == [1, 3]
    assert plan.coverage_ratio == 1.0


async def test_graph_replans_for_rule_gap_and_stops_without_new_candidates() -> None:
    class TrackingSearchModel(RuleBasedResumeGenerationModel):
        def __init__(self) -> None:
            self.gap_history: list[list[str]] = []

        async def plan_search(
            self,
            analysis: JDAnalysisSnapshot,
            *,
            gap_coverage_ids: list[str],
            search_round: int,
            top_k: int,
        ) -> list[SearchTask]:
            self.gap_history.append(list(gap_coverage_ids))
            return await super().plan_search(
                analysis,
                gap_coverage_ids=gap_coverage_ids,
                search_round=search_round,
                top_k=top_k,
            )

    source = _source()
    model = TrackingSearchModel()
    experiences = [
        _experience(
            1,
            11,
            action="使用 Python 和 FastAPI 设计 API",
            result="完成服务交付",
            technologies=["Python", "FastAPI"],
        )
    ]
    graph = build_resume_generation_graph(
        ResumeGenerationGraphDependencies(
            model=model,
            retriever=_qdrant_retriever(experiences),
        )
    ).compile()

    state = await graph.ainvoke(
        {
            "jd_source": source,
            "experiences": experiences,
            "constraints": ResumeConstraints(max_search_rounds=3),
        }
    )

    assert state["plan"].search_rounds == 2
    assert model.gap_history == [[], ["requirement-2"]]
    assert "requirement-2" in state["plan"].uncovered_requirements
    assert state["plan"].review_actions == ["accept_with_gaps"]
    assert "critique_plan" not in graph.get_graph().nodes
    assert state["validation"].valid is True
    assert state["provenance"].bullets[0].evidence_ids == [11]


async def test_graph_fact_checks_draft_before_returning_materialized_preview() -> None:
    class RejectingFactCheckModel(RuleBasedResumeGenerationModel):
        async def validate_draft(self, draft, plan, experiences):
            return DraftFactCheckResult(
                model_used=True,
                model_required=True,
                assessments=[
                    DraftClaimAssessment(
                        claim_id="summary",
                        verdict="supported",
                        reason="Summary 有 Evidence 支持",
                    ),
                    DraftClaimAssessment(
                        claim_id="experience:1:bullet:0",
                        verdict="unsupported",
                        unsupported_fragments=["完成服务交付"],
                        reason="模型认为结果缺少充分依据",
                    ),
                ],
            )

    experiences = [
        _experience(
            1,
            11,
            action="使用 Python 和 FastAPI 设计 API",
            result="完成服务交付",
            technologies=["Python", "FastAPI"],
        )
    ]
    graph = build_resume_generation_graph(
        ResumeGenerationGraphDependencies(
            model=RejectingFactCheckModel(),
            retriever=_qdrant_retriever(experiences),
        )
    ).compile()

    state = await graph.ainvoke(
        {
            "jd_source": _source(),
            "experiences": experiences,
            "constraints": ResumeConstraints(max_search_rounds=1),
        }
    )

    assert state["resume_data"].personalProjects
    assert state["validation"].valid is False
    assert state["validation"].model_validation_status == "completed"
    assert any(
        check.source == "model"
        and check.claim_id == "experience:1:bullet:0"
        and check.status == "failed"
        for check in state["validation"].checks
    )


async def test_graph_logs_evidence_scoring_with_candidate_context(caplog) -> None:
    experiences = [
        _experience(
            1,
            11,
            action="使用 Python 和 FastAPI 设计 API",
            result="完成服务交付",
            technologies=["Python", "FastAPI"],
        )
    ]
    graph = build_resume_generation_graph(
        ResumeGenerationGraphDependencies(
            model=RuleBasedResumeGenerationModel(),
            retriever=_qdrant_retriever(experiences),
        )
    ).compile()
    caplog.set_level(
        logging.INFO,
        logger="app.resume_generation.observability",
    )

    state = await graph.ainvoke(
        {
            "run_id": "run-score-1",
            "jd_source": _source(),
            "experiences": experiences,
            "constraints": ResumeConstraints(max_search_rounds=1),
        }
    )

    retrieval_events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "app.resume_generation.observability"
        and json.loads(record.getMessage()).get("event")
        == "resume_generation.retrieval"
    ]
    events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "app.resume_generation.observability"
        and json.loads(record.getMessage()).get("event")
        == "resume_generation.evidence_scoring"
    ]
    assert retrieval_events
    assert all(event["run_id"] == "run-score-1" for event in retrieval_events)
    assert all(event["search_round"] == 1 for event in retrieval_events)
    assert len(events) == 1
    event = events[0]
    assert event["run_id"] == "run-score-1"
    assert event["search_round"] == 1
    assert event["candidate_scope"] == "cumulative"
    assert event["judge_model"] == {
        "class": "RuleBasedResumeGenerationModel",
        "fallback_used": False,
    }
    judgment = event["judgments"][0]
    expected_judgment = state["judgments"][0].model_dump(mode="json")
    assert judgment["evidence_id"] == 11
    assert judgment["best_retrieval_score"] == 0.9
    assert (
        judgment["best_retrieval_score_scope"]
        == "max_across_tasks_and_completed_rounds"
    )
    for field, value in expected_judgment.items():
        assert judgment[field] == value


def test_reference_validation_only_checks_bound_evidence_ids() -> None:
    source = _source()
    analysis = JDAnalysisSnapshot(
        source=source,
        target_title=source.job_name,
        coverage_items=[
            CoverageItem(
                coverage_id="python",
                source_requirement_ids=[1],
                statement="Python FastAPI",
                importance="must",
                capability="Python FastAPI",
            )
        ],
    )
    experiences = [
        _experience(
            1,
            11,
            action="使用 Python 和 FastAPI 开发接口",
            result="完成交付",
            technologies=["Python", "FastAPI"],
        )
    ]
    plan = assemble_plan(
        analysis,
        experiences,
        [
            EvidenceJudgment(
                evidence_id=11,
                experience_id=1,
                coverage_item_ids=["python"],
                relevance=0.9,
                evidence_strength=0.8,
                uniqueness=0.5,
                supported_skills=["Python", "FastAPI"],
            )
        ],
        ResumeConstraints(),
        search_rounds=1,
    )
    checks = validate_draft_references(
        plan,
        ResumeDraft(
            experiences=[
                DraftedExperience(
                    experience_id=1,
                    bullets=[
                        DraftBullet(
                            experience_id=1,
                            evidence_ids=[11],
                            text="使用 Python 和 FastAPI 开发接口，性能提升 50%",
                        )
                    ],
                )
            ]
        ),
        experiences,
    )

    assert [check.status for check in checks] == ["passed"]


def test_reference_validation_rejects_unknown_evidence() -> None:
    source = _source()
    analysis = JDAnalysisSnapshot(
        source=source,
        target_title=source.job_name,
        coverage_items=[
            CoverageItem(
                coverage_id="python",
                source_requirement_ids=[1],
                statement="Python FastAPI",
                importance="must",
                capability="Python FastAPI",
            )
        ],
    )
    experiences = [
        _experience(
            1,
            11,
            action="使用 Python 开发接口",
            result="完成交付",
            technologies=["Python"],
        )
    ]
    plan = assemble_plan(
        analysis,
        experiences,
        [
            EvidenceJudgment(
                evidence_id=11,
                experience_id=1,
                coverage_item_ids=["python"],
                relevance=0.9,
                evidence_strength=0.8,
                uniqueness=0.5,
            )
        ],
        ResumeConstraints(),
        search_rounds=1,
    )
    checks = validate_draft_references(
        plan,
        ResumeDraft(
            experiences=[
                DraftedExperience(
                    experience_id=1,
                    bullets=[
                        DraftBullet(
                            experience_id=1,
                            evidence_ids=[999],
                            text="使用 Python 开发接口",
                        )
                    ],
                )
            ]
        ),
        experiences,
    )

    assert [check.status for check in checks] == ["failed"]
    assert "Evidence 999" in checks[0].message


def test_reference_validation_rejects_cross_experience_attribution() -> None:
    """合法 Evidence ID 也不能挂到声明不一致的 Experience 上。"""
    source = _source()
    analysis = JDAnalysisSnapshot(
        source=source,
        target_title=source.job_name,
        coverage_items=[
            CoverageItem(
                coverage_id="python",
                source_requirement_ids=[1],
                statement="Python FastAPI",
                importance="must",
                capability="Python FastAPI",
            )
        ],
    )
    experiences = [
        _experience(
            1,
            11,
            action="使用 Python 开发接口",
            result="完成交付",
            technologies=["Python"],
        )
    ]
    plan = assemble_plan(
        analysis,
        experiences,
        [
            EvidenceJudgment(
                evidence_id=11,
                experience_id=1,
                coverage_item_ids=["python"],
                relevance=0.9,
                evidence_strength=0.8,
                uniqueness=0.5,
            )
        ],
        ResumeConstraints(),
        search_rounds=1,
    )
    checks = validate_draft_references(
        plan,
        ResumeDraft(
            experiences=[
                DraftedExperience(
                    experience_id=1,
                    bullets=[
                        DraftBullet(
                            experience_id=2,
                            evidence_ids=[11],
                            text="使用 Python 开发接口",
                        )
                    ],
                )
            ]
        ),
        experiences,
    )

    assert [check.status for check in checks] == ["failed"]
    assert "Experience 与所属 Draft 经历不一致" in checks[0].message


def test_materialize_does_not_silently_change_validated_bullets() -> None:
    """拼装必须保留校验过的 Bullet，使最终文本与 provenance 可一一复现。"""
    source = _source()
    analysis = JDAnalysisSnapshot(
        source=source,
        target_title=source.job_name,
        coverage_items=[],
    )
    experiences = [
        _experience(
            1,
            11,
            action="使用 Python 开发接口",
            result="完成交付",
            technologies=["Python"],
        )
    ]
    plan = ResumePlan(
        selected_experiences=[
            PlannedExperience(
                experience_id=1,
                section="personalProjects",
                evidence_ids=[11],
                bullet_budget=1,
            )
        ],
        search_rounds=1,
        coverage_ratio=1,
    )
    resume_data, provenance = materialize_resume(
        analysis,
        plan,
        ResumeDraft(
            experiences=[
                DraftedExperience(
                    experience_id=1,
                    bullets=[
                        DraftBullet(
                            experience_id=1,
                            evidence_ids=[11],
                            text="使用 Python 开发接口",
                        ),
                        DraftBullet(
                            experience_id=1,
                            evidence_ids=[11],
                            text="完成接口交付",
                        ),
                    ],
                )
            ]
        ),
        experiences,
    )

    assert resume_data.personalProjects[0].description == [
        "使用 Python 开发接口",
        "完成接口交付",
    ]
    assert len(provenance.bullets) == 2
    assert provenance.bullets[0].bullet_index == 0
    assert provenance.bullets[0].evidence_ids == [11]
    assert provenance.bullets[1].bullet_index == 1
    assert provenance.bullets[1].evidence_ids == [11]


async def test_auto_model_records_deterministic_fallback_in_validation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenAnalyzer(RuleBasedResumeGenerationModel):
        async def analyze_jd(self, source):
            raise RuntimeError("model unavailable")

    model = FallbackResumeGenerationModel(
        BrokenAnalyzer(), RuleBasedResumeGenerationModel()
    )
    experiences = [
        _experience(
            1,
            11,
            action="使用 Python 和 FastAPI 设计 API",
            result="完成交付",
            technologies=["Python", "FastAPI"],
        )
    ]
    graph = build_resume_generation_graph(
        ResumeGenerationGraphDependencies(
            model=model,
            retriever=_qdrant_retriever(experiences),
        )
    ).compile()
    with caplog.at_level(logging.ERROR, logger="app.resume_generation.model"):
        state = await graph.ainvoke(
            {
                "jd_source": _source(),
                "experiences": experiences,
                "constraints": ResumeConstraints(max_search_rounds=1),
            }
        )

    assert any("analyze_jd" in warning for warning in state["validation"].warnings)
    assert state["validation"].valid is False
    assert state["validation"].model_validation_status == "failed"
    assert model.fallback_errors == [
        {
            "stage": "analyze_jd",
            "primary_model": "BrokenAnalyzer",
            "fallback_model": "RuleBasedResumeGenerationModel",
            "exception_type": "RuntimeError",
            "message": "model unavailable",
        }
    ]
    record = next(
        item for item in caplog.records if "Resume generation stage" in item.message
    )
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert "analyze_jd" in record.message
    assert "RuntimeError: model unavailable" in record.message
