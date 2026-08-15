"""由 Experience CRUD 事件驱动的 Evidence 索引维护。"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from qdrant_client import models
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.background_jobs.repository import OutboxRepository
from app.experience.models import ExperienceItem
from app.experience.repositories.evidence_repository import EvidenceRepository
from app.experience.repositories.experience_repository import ExperienceRepository
from app.resume_generation.retriever import (
    QdrantEvidenceStore,
    _to_langchain_document,
    build_documents,
)
from app.resume_generation.schemas import EvidenceSnapshot, ExperienceSnapshot

INDEX_FUNCTION = "sync_resume_evidence_index"
INDEX_TOPIC = "resume.evidence_index"


async def enqueue_experience_index_sync(
    session: AsyncSession, experience_id: int
) -> None:
    """在 Experience 写事务内记录索引同步意图。"""
    await OutboxRepository(session).enqueue(
        topic=INDEX_TOPIC,
        dedupe_key=f"resume-index:{experience_id}:{uuid4()}",
        payload={"experience_id": experience_id},
    )


async def enqueue_index_bootstrap(session: AsyncSession) -> int:
    """Worker 启动时为既有数据补发事件，兼容功能上线前创建的经历。"""
    experience_ids = list(await session.scalars(select(ExperienceItem.experience_id)))
    for experience_id in experience_ids:
        await enqueue_experience_index_sync(session, experience_id)
    return len(experience_ids)


async def load_ready_experience_snapshot(
    session: AsyncSession, experience_id: int
) -> ExperienceSnapshot | None:
    """读取索引所需的当前事实；非 ready 或无证据表示应从索引删除。"""
    experience = await ExperienceRepository(session).get(experience_id)
    if experience is None or experience.status != "ready":
        return None
    evidence = await EvidenceRepository(session).list_for_experience(experience_id)
    if not evidence:
        return None
    return ExperienceSnapshot(
        experience_id=experience.experience_id,
        kind=experience.kind,
        title=experience.title,
        organization=experience.organization,
        role=experience.role,
        location=experience.location,
        start_date=experience.start_date,
        end_date=experience.end_date,
        is_current=experience.is_current,
        background=experience.background,
        technologies=list(experience.technologies or []),
        tags=list(experience.tags or []),
        status="ready",
        completeness=experience.completeness,
        updated_at=experience.updated_at,
        evidence=[
            EvidenceSnapshot(
                evidence_id=item.id,
                background=item.background,
                action=item.action,
                result=item.result,
                updated_at=item.updated_at,
            )
            for item in evidence
        ],
    )


class QdrantEvidenceIndexer:
    """按 Experience 聚合替换 Qdrant points，避免遗留已删除 Evidence。"""

    def __init__(self, backend: QdrantEvidenceStore) -> None:
        self._backend = backend

    async def sync(self, experience_id: int, snapshot: ExperienceSnapshot | None) -> None:
        store = await self._backend.ensure_store()
        documents = build_documents([snapshot]) if snapshot is not None else []
        async with self._backend.index_lock:
            await asyncio.to_thread(
                self._replace_experience,
                store,
                experience_id,
                documents,
            )

    def _replace_experience(self, store, experience_id: int, documents) -> None:
        client = self._backend.client
        if client is None:
            raise RuntimeError("Qdrant evidence store has no client")
        client.delete(
            collection_name=self._backend.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="metadata.experience_id",
                            match=models.MatchValue(value=experience_id),
                        )
                    ]
                )
            ),
            wait=True,
        )
        if documents:
            store.add_documents(
                [
                    _to_langchain_document(item, self._backend.index_signature)
                    for item in documents
                ],
                ids=[item.evidence_id for item in documents],
            )
