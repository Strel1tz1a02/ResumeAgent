"""独立 ARQ Memory Worker 入口。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

from arq.connections import RedisSettings
from arq.worker import func

from app import database as database_module
from app.ai_chat.memory.services.memory_service import MemoryService
from app.ai_chat.memory.settings import memory_settings
from app.ai_chat.models import AiChatRun
from app.background_jobs.dispatcher import OutboxDispatcher, OutboxRoute
from app.background_jobs.repository import OutboxRepository
from app.background_jobs.settings import background_job_settings

logger = logging.getLogger(__name__)

_COMPACT_FUNCTION = "compact_memory"
_COMPACT_TOPIC = "memory.compact"


async def _mark_outbox_processed(outbox_id: int) -> None:
    """在 Snapshot 已持久化后确认 Outbox。"""
    async with database_module.db.session() as session:
        await OutboxRepository(session).mark_processed(outbox_id)
        await session.commit()


async def _conversation_id(run_id: int) -> int | None:
    """读取分布式锁所需的会话身份。"""
    async with database_module.db.session() as session:
        run = await session.get(AiChatRun, run_id)
        return run.conversation_id if run is not None else None


async def compact_memory(
    ctx: dict[str, Any],
    outbox_id: int,
    payload: dict[str, Any],
) -> None:
    """管理后台任务怎么安全执行 compact_run"""
    run_id = payload.get("run_id")
    if isinstance(run_id, bool) or not isinstance(run_id, int):
        raise TypeError("memory.compact payload requires an integer run_id")
    conversation_id = await _conversation_id(run_id)
    if conversation_id is None:
        logger.info("Discarding obsolete memory job: run=%s", run_id)
        await _mark_outbox_processed(outbox_id)
        return

    redis = ctx.get("redis")
    if redis is None:
        raise RuntimeError("ARQ worker context has no Redis connection")
    lock = redis.lock(
        f"ai-chat:memory:conversation:{conversation_id}",
        timeout=memory_settings.ai_chat_memory_job_timeout_seconds + 60,
        blocking_timeout=memory_settings.ai_chat_memory_job_timeout_seconds,
    )
    async with lock:
        try:
            compacted = await MemoryService().compact_run(run_id)
        except LookupError:
            compacted = False
            logger.info("Discarding obsolete memory job: run=%s", run_id)
    if not compacted:
        logger.info("Memory job target is not a terminal history Run: run=%s", run_id)
    await _mark_outbox_processed(outbox_id)


async def startup(ctx: dict[str, Any]) -> None:
    """启动只负责发布 Outbox 的轻量循环。"""
    stop = asyncio.Event()
    dispatcher = OutboxDispatcher(
        {
            _COMPACT_TOPIC: OutboxRoute(
                function=_COMPACT_FUNCTION,
                queue_name=memory_settings.ai_chat_memory_queue_name,
            )
        }
    )
    ctx["outbox_stop"] = stop
    ctx["outbox_task"] = asyncio.create_task(
        dispatcher.run(ctx["redis"], stop),
        name="background-outbox-dispatcher",
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    """停止 Dispatcher 并释放 Worker 数据库连接。"""
    stop = ctx.get("outbox_stop")
    task = ctx.get("outbox_task")
    if isinstance(stop, asyncio.Event):
        stop.set()
    if isinstance(task, asyncio.Task):
        await task
    await database_module.db.close()


class WorkerSettings:
    """ARQ CLI 读取的专用 Memory Worker 配置。"""

    functions: ClassVar[list[Any]] = [
        func(
            compact_memory,
            name=_COMPACT_FUNCTION,
            timeout=memory_settings.ai_chat_memory_job_timeout_seconds,
            max_tries=1,
            keep_result=0,
        )
    ]
    redis_settings = RedisSettings.from_dsn(background_job_settings.redis_url) # 设置监听的 Redis
    queue_name = memory_settings.ai_chat_memory_queue_name
    max_jobs = memory_settings.ai_chat_memory_worker_concurrency
    job_timeout = memory_settings.ai_chat_memory_job_timeout_seconds
    max_tries = 1
    retry_jobs = False
    keep_result = 0
    health_check_interval = 30
    health_check_key = "ai-chat:memory:health"
    job_completion_wait = 30
    on_startup = startup
    on_shutdown = shutdown
