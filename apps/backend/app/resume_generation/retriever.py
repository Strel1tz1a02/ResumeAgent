"""Evidence 检索边界与 Qdrant 原生混合召回实现。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any, Protocol

from fastembed import TextEmbedding
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_qdrant import FastEmbedSparse, QdrantVectorStore, RetrievalMode
from qdrant_client import QdrantClient, models

from app.resume_generation.observability import log_generation_trace
from app.resume_generation.schemas import (
    EvidenceDocument,
    ExperienceSnapshot,
    RetrievedEvidence,
    SearchTask,
)

_LATIN_RE = re.compile(r"[a-z0-9][a-z0-9+#._/-]*", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u3400-\u9fff]+")


def tokenize(text: str) -> set[str]:
    """提取 Judge 使用的英文词项、中文单字和二元组，不参与召回打分。"""
    normalized = text.casefold()
    terms = {match.group(0) for match in _LATIN_RE.finditer(normalized)}
    for match in _CJK_RE.finditer(normalized):
        segment = match.group(0)
        terms.update(segment)
        terms.update(segment[index : index + 2] for index in range(len(segment) - 1))
    return {term for term in terms if term.strip()}


def build_documents(experiences: list[ExperienceSnapshot]) -> list[EvidenceDocument]:
    """把每条 Evidence 与所属 Experience 上下文组装成独立检索文档。"""
    documents: list[EvidenceDocument] = []
    for experience in experiences:
        end = "至今" if experience.is_current else (experience.end_date or "")
        dates = " - ".join(part for part in (experience.start_date, end) if part)
        for evidence in experience.evidence:
            documents.append(
                EvidenceDocument(
                    evidence_id=evidence.evidence_id,
                    experience_id=experience.experience_id,
                    kind=experience.kind,
                    title=experience.title,
                    organization=experience.organization,
                    role=experience.role,
                    dates=dates,
                    experience_background=experience.background,
                    evidence_background=evidence.background,
                    action=evidence.action,
                    result=evidence.result,
                    technologies=experience.technologies,
                    tags=experience.tags,
                )
            )
    return documents


class EvidenceRetriever(Protocol):
    async def retrieve(
        self,
        tasks: list[SearchTask],
        documents: list[EvidenceDocument],
    ) -> list[RetrievedEvidence]: ...


class FastEmbedDenseEmbeddings(Embeddings):
    """把 FastEmbed 中文模型适配为 LangChain Embeddings。"""

    def __init__(self, model_name: str) -> None:
        self._model = TextEmbedding(model_name=model_name)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """为待索引 Evidence 生成稠密向量。"""
        return [vector.tolist() for vector in self._model.passage_embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        """为单个检索问题生成稠密向量。"""
        return next(iter(self._model.query_embed(text))).tolist()


class QdrantEvidenceStore:
    """封装 Qdrant 集合和向量模型，供索引器与检索器共享。"""

    def __init__(
        self,
        *,
        url: str = "http://localhost:6333",
        api_key: str = "",
        collection_name: str = "resume_evidence",
        dense_model: str = "BAAI/bge-small-zh-v1.5",
        sparse_model: str = "Qdrant/bm25",
        timeout_seconds: int = 20,
        vector_store: Any | None = None,
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._collection_name = collection_name
        self._dense_model = dense_model
        self._sparse_model = sparse_model
        self._index_signature = f"qdrant-hybrid-v1:{dense_model}:{sparse_model}"
        self._timeout_seconds = timeout_seconds
        self._store: Any | None = vector_store
        self._client: QdrantClient | None = (
            getattr(vector_store, "client", None) if vector_store is not None else None
        )
        self._manage_collection = vector_store is None
        self._initialization_lock = asyncio.Lock()
        self.index_lock = asyncio.Lock()

    @property
    def client(self) -> QdrantClient | None:
        return self._client

    @property
    def collection_name(self) -> str:
        return self._collection_name

    @property
    def index_signature(self) -> str:
        return self._index_signature

    async def ensure_store(self) -> Any:
        """延迟初始化模型和连接，避免应用启动时下载模型或依赖 Qdrant。"""
        if self._store is not None:
            return self._store
        async with self._initialization_lock:
            if self._store is None:
                self._store = await asyncio.to_thread(self._build_store)
        return self._store

    def _build_store(self) -> QdrantVectorStore:
        """创建或连接同时包含 dense、sparse 命名向量的集合。"""
        embeddings = FastEmbedDenseEmbeddings(self._dense_model)
        sparse_embeddings = FastEmbedSparse(model_name=self._sparse_model)
        client = QdrantClient(
            url=self._url,
            api_key=self._api_key or None,
            timeout=self._timeout_seconds,
        )
        if not client.collection_exists(self._collection_name):
            dimension = len(embeddings.embed_query("向量维度探测"))
            client.create_collection(
                collection_name=self._collection_name,
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
        self._client = client
        return QdrantVectorStore(
            client=client,
            collection_name=self._collection_name,
            embedding=embeddings,
            sparse_embedding=sparse_embeddings,
            retrieval_mode=RetrievalMode.HYBRID,
            vector_name="dense",
            sparse_vector_name="sparse",
        )


class QdrantEvidenceRetriever:
    """只查询既有 Qdrant 索引，不在生成请求中修改索引。"""

    def __init__(
        self,
        *,
        backend: QdrantEvidenceStore | None = None,
        url: str = "http://localhost:6333",
        api_key: str = "",
        collection_name: str = "resume_evidence",
        dense_model: str = "BAAI/bge-small-zh-v1.5",
        sparse_model: str = "Qdrant/bm25",
        timeout_seconds: int = 20,
        vector_store: Any | None = None,
    ) -> None:
        self._backend = backend or QdrantEvidenceStore(
            url=url,
            api_key=api_key,
            collection_name=collection_name,
            dense_model=dense_model,
            sparse_model=sparse_model,
            timeout_seconds=timeout_seconds,
            vector_store=vector_store,
        )
        # 保留测试和诊断读取入口；索引写入由 QdrantEvidenceIndexer 负责。
        self._store = vector_store

    async def retrieve(
        self,
        tasks: list[SearchTask],
        documents: list[EvidenceDocument],
    ) -> list[RetrievedEvidence]:
        """对当前可用 Evidence ID 执行 Qdrant 原生混合查询。"""
        return await self._retrieve(
            tasks,
            documents,
            run_id=None,
            search_round=None,
            trace_enabled=False,
        )

    async def retrieve_with_trace(
        self,
        tasks: list[SearchTask],
        documents: list[EvidenceDocument],
        *,
        run_id: str | None,
        search_round: int | None,
    ) -> list[RetrievedEvidence]:
        """执行召回，并为完整生成链路附加 Run 与轮次上下文。

        Args:
            tasks: 本轮模型规划出的检索任务。
            documents: 本次生成允许召回的 Evidence 文档。
            run_id: 简历生成 Run ID。
            search_round: 当前检索轮次。
        """
        return await self._retrieve(
            tasks,
            documents,
            run_id=run_id,
            search_round=search_round,
            trace_enabled=True,
        )

    async def _retrieve(
        self,
        tasks: list[SearchTask],
        documents: list[EvidenceDocument],
        *,
        run_id: str | None,
        search_round: int | None,
        trace_enabled: bool,
    ) -> list[RetrievedEvidence]:
        """执行逐问题召回，并按需在跨问题合并前保留诊断数据。

        Args:
            tasks: 本轮模型规划出的检索任务。
            documents: 本次生成允许召回的 Evidence 文档。
            run_id: 简历生成 Run ID。
            search_round: 当前检索轮次。
            trace_enabled: 是否记录包含经历正文的召回日志。
        """
        if not tasks or not documents:
            if trace_enabled:
                log_generation_trace(
                    "resume_generation.retrieval",
                    run_id=run_id,
                    search_round=search_round,
                    payload={
                        "status": "skipped",
                        "skip_reason": (
                            "no_search_tasks"
                            if not tasks
                            else "no_allowed_documents"
                        ),
                        "question": None,
                        "retrieval_mode": "hybrid",
                        "fusion": "rrf",
                        "allowed_evidence_id_count": len(documents),
                        "hit_count": 0,
                        "hits": [],
                    },
                )
            return []

        store = await self._backend.ensure_store()
        self._store = store

        evidence_ids = [document.evidence_id for document in documents]
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="metadata.evidence_id",
                    match=models.MatchAny(any=evidence_ids),
                )
            ]
        )
        merged: dict[int, RetrievedEvidence] = {}
        for task in tasks:
            found = await asyncio.to_thread(
                store.similarity_search_with_score,
                task.query,
                task.top_k,
                filter=query_filter,
                hybrid_fusion=models.FusionQuery(fusion=models.Fusion.RRF),
            )
            if trace_enabled:
                self._log_task_results(
                    task,
                    found,
                    run_id=run_id,
                    search_round=search_round,
                    allowed_evidence_id_count=len(evidence_ids),
                )
            self._merge_task_results(merged, task, found)

        return sorted(
            merged.values(),
            key=lambda item: (-item.retrieval_score, item.document.evidence_id),
        )

    @staticmethod
    def _log_task_results(
        task: SearchTask,
        found: list[tuple[Document, float]],
        *,
        run_id: str | None,
        search_round: int | None,
        allowed_evidence_id_count: int,
    ) -> None:
        """记录一个检索问题对应的原始片段、排名与 RRF 融合分。

        Args:
            task: 触发本次 Qdrant 查询的检索任务。
            found: 尚未跨问题合并的 Qdrant 命中结果。
            run_id: 简历生成 Run ID。
            search_round: 当前检索轮次。
            allowed_evidence_id_count: 传给 Qdrant 过滤器的 Evidence ID 数量。
        """
        hits: list[dict[str, Any]] = []
        for rank, (langchain_document, score) in enumerate(found, start=1):
            document = EvidenceDocument.model_validate(
                langchain_document.metadata["evidence"]
            )
            raw_score = float(score)
            hits.append(
                {
                    "rank": rank,
                    "evidence_id": document.evidence_id,
                    "experience_id": document.experience_id,
                    "qdrant_fused_score_raw": raw_score,
                    "retrieval_score": max(0.0, min(1.0, raw_score)),
                    "searchable_text": langchain_document.page_content,
                }
            )
        log_generation_trace(
            "resume_generation.retrieval",
            run_id=run_id,
            search_round=search_round,
            payload={
                "status": "completed",
                "question": {
                    "task_id": task.task_id,
                    "coverage_item_ids": task.coverage_item_ids,
                    "intent": task.intent,
                    "query": task.query,
                    "requested_filters": task.filters,
                    "top_k": task.top_k,
                },
                "retrieval_mode": "hybrid",
                "fusion": "rrf",
                "allowed_evidence_id_count": allowed_evidence_id_count,
                "hit_count": len(hits),
                "hits": hits,
            },
        )

    @staticmethod
    def _merge_task_results(
        merged: dict[int, RetrievedEvidence],
        task: SearchTask,
        found: list[tuple[Document, float]],
    ) -> None:
        """跨 SearchTask 按 Evidence ID 去重，保留最高 Qdrant 融合分。"""
        for langchain_document, score in found:
            document = EvidenceDocument.model_validate(
                langchain_document.metadata["evidence"]
            )
            normalized_score = max(0.0, min(1.0, float(score)))
            existing = merged.get(document.evidence_id)
            if existing is None:
                merged[document.evidence_id] = RetrievedEvidence(
                    document=document,
                    retrieval_score=normalized_score,
                    matched_terms=[],
                    task_ids=[task.task_id],
                )
                continue
            existing.retrieval_score = max(existing.retrieval_score, normalized_score)
            if task.task_id not in existing.task_ids:
                existing.task_ids.append(task.task_id)


def _document_hash(document: EvidenceDocument, index_signature: str = "") -> str:
    """生成稳定内容摘要，防止未变化 Evidence 重复向量化。"""
    raw = json.dumps(
        {
            "document": document.model_dump(mode="json"),
            "index_signature": index_signature,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _to_langchain_document(
    document: EvidenceDocument,
    index_signature: str = "",
) -> Document:
    """转换检索文本和可追溯 payload。"""
    return Document(
        page_content=document.searchable_text(),
        metadata={
            "evidence_id": document.evidence_id,
            "experience_id": document.experience_id,
            "kind": document.kind,
            "status": "ready",
            "content_hash": _document_hash(document, index_signature),
            "evidence": document.model_dump(mode="json"),
        },
    )


def merge_retrieval_rounds(
    previous: list[RetrievedEvidence], current: list[RetrievedEvidence]
) -> list[RetrievedEvidence]:
    """按 Evidence ID 合并多轮结果，保留最高分和全部命中来源。"""
    by_id: dict[int, RetrievedEvidence] = {
        item.document.evidence_id: item.model_copy(deep=True) for item in previous
    }
    for item in current:
        found = by_id.get(item.document.evidence_id)
        if found is None:
            by_id[item.document.evidence_id] = item.model_copy(deep=True)
            continue
        found.retrieval_score = max(found.retrieval_score, item.retrieval_score)
        found.matched_terms = sorted(set(found.matched_terms) | set(item.matched_terms))
        found.task_ids = sorted(set(found.task_ids) | set(item.task_ids))
    return sorted(
        by_id.values(),
        key=lambda item: (-item.retrieval_score, item.document.evidence_id),
    )
