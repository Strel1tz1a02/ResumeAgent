"""简历 Evidence 索引专用 ARQ Worker。"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from arq.connections import RedisSettings
from arq.worker import func

from app import database as database_module
from app.background_jobs.dispatcher import OutboxDispatcher, OutboxRoute
from app.background_jobs.repository import OutboxRepository
from app.background_jobs.settings import background_job_settings
from app.config import settings
from app.resume_generation.indexing import (
    INDEX_FUNCTION,
    INDEX_TOPIC,
    QdrantEvidenceIndexer,
    enqueue_index_bootstrap,
    load_ready_experience_snapshot,
)
from app.resume_generation.retriever import QdrantEvidenceStore

_QUEUE_NAME = "resume:evidence-index"
_JOB_TIMEOUT_SECONDS = 300


def _indexer() -> QdrantEvidenceIndexer:
    return QdrantEvidenceIndexer(
        QdrantEvidenceStore(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            collection_name=settings.qdrant_collection,
            dense_model=settings.qdrant_dense_model,
            sparse_model=settings.qdrant_sparse_model,
            timeout_seconds=settings.qdrant_timeout_seconds,
        )
    )


async def sync_resume_evidence_index(
    ctx: dict[str, Any], outbox_id: int, payload: dict[str, Any]
) -> None:
    """以数据库当前状态为准，幂等替换一个 Experience 的索引。"""
    experience_id = payload.get("experience_id")
    if isinstance(experience_id, bool) or not isinstance(experience_id, int):
        raise TypeError("resume.evidence_index requires an integer experience_id")
    async with database_module.db.session() as session:
        snapshot = await load_ready_experience_snapshot(session, experience_id)
    indexer = ctx.get("resume_indexer")
    if not isinstance(indexer, QdrantEvidenceIndexer):
        raise TypeError("resume index worker has no indexer")
    await indexer.sync(experience_id, snapshot)
    async with database_module.db.session() as session:
        await OutboxRepository(session).mark_processed(outbox_id)
        await session.commit()


async def startup(ctx: dict[str, Any]) -> None:
    async with database_module.db.session() as session:
        await enqueue_index_bootstrap(session)
        await session.commit()
    stop = asyncio.Event()
    dispatcher = OutboxDispatcher(
        {
            INDEX_TOPIC: OutboxRoute(
                function=INDEX_FUNCTION,
                queue_name=_QUEUE_NAME,
            )
        }
    )
    ctx["resume_indexer"] = _indexer()
    ctx["outbox_stop"] = stop
    ctx["outbox_task"] = asyncio.create_task(
        dispatcher.run(ctx["redis"], stop), name="resume-index-outbox-dispatcher"
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    stop = ctx.get("outbox_stop")
    task = ctx.get("outbox_task")
    if isinstance(stop, asyncio.Event):
        stop.set()
    if isinstance(task, asyncio.Task):
        await task
    await database_module.db.close()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        func(
            sync_resume_evidence_index,
            name=INDEX_FUNCTION,
            timeout=_JOB_TIMEOUT_SECONDS,
            max_tries=3,
            keep_result=0,
        )
    ]
    redis_settings = RedisSettings.from_dsn(background_job_settings.redis_url)
    queue_name = _QUEUE_NAME
    max_jobs = 1
    job_timeout = _JOB_TIMEOUT_SECONDS
    max_tries = 3
    retry_jobs = True
    keep_result = 0
    health_check_interval = 30
    health_check_key = "resume:evidence-index:health"
    job_completion_wait = 30
    on_startup = startup
    on_shutdown = shutdown
