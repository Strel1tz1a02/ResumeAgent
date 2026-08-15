"""简历生成检索、组合和 Graph 停止条件。"""

from typing import Any

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
    RuleBasedResumeGenerationModel,
)
from app.resume_generation.planner import (
    assemble_plan,
    materialize_resume,
    validate_generation,
)
from app.resume_generation.retriever import (
    QdrantEvidenceRetriever,
    QdrantEvidenceStore,
    _to_langchain_document,
    build_documents,
)
from app.resume_generation.schemas import (
    CoverageItem,
    DraftBullet,
    DraftedExperience,
    EvidenceJudgment,
    EvidenceSnapshot,
    ExperienceSnapshot,
    JDAnalysisSnapshot,
    JDAnalysisSourceSnapshot,
    JDRequirementSnapshot,
    PlanCritique,
    ResumeConstraints,
    ResumeDraft,
    SearchTask,
)


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


async def test_graph_replans_once_and_keeps_uncovered_gap_explicit() -> None:
    source = _source()
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

    state = await graph.ainvoke(
        {
            "jd_source": source,
            "experiences": experiences,
            "constraints": ResumeConstraints(max_search_rounds=2),
        }
    )

    assert state["plan"].search_rounds == 2
    assert "requirement-2" in state["plan"].uncovered_requirements
    assert state["validation"].valid is True
    assert state["provenance"].bullets[0].evidence_ids == [11]


async def test_graph_uses_model_defined_gap_for_replanning() -> None:
    class ModelDirectedCritique(RuleBasedResumeGenerationModel):
        async def critique(
            self,
            analysis,
            plan,
            judgments,
            constraints,
            *,
            search_round,
            has_new_candidates,
        ):
            if search_round == 1:
                return PlanCritique(
                    acceptable=False,
                    actions=["search_more"],
                    gap_coverage_ids=["requirement-1"],
                    warnings=["当前证据虽有映射，但支持强度仍不足"],
                )
            return PlanCritique(acceptable=True)

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
            model=ModelDirectedCritique(),
            retriever=_qdrant_retriever(experiences),
        )
    ).compile()

    state = await graph.ainvoke(
        {
            "jd_source": _source(),
            "experiences": experiences,
            "constraints": ResumeConstraints(max_search_rounds=2),
        }
    )

    assert state["plan"].search_rounds == 2
    assert state["all_search_tasks"][-1].coverage_item_ids == ["requirement-1"]
    assert state["plan"].uncovered_requirements == []


async def test_validation_rejects_number_absent_from_bound_evidence() -> None:
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
                            text="使用 Python 和 FastAPI 开发接口，性能提升 50%",
                        )
                    ],
                )
            ]
        ),
        experiences,
    )

    validation = validate_generation(
        plan, resume_data, provenance, experiences, ResumeConstraints()
    )

    assert validation.valid is False
    assert any("50%" in error for error in validation.errors)


async def test_auto_model_records_deterministic_fallback_in_validation() -> None:
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
    state = await graph.ainvoke(
        {
            "jd_source": _source(),
            "experiences": experiences,
            "constraints": ResumeConstraints(max_search_rounds=1),
        }
    )

    assert any("analyze_jd" in warning for warning in state["validation"].warnings)
