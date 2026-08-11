"""定期扫描待处理 Outbox，然后调用 ARQ，把任务放进 Redis。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app import database as database_module
from app.background_jobs.repository import OutboxRepository
from app.background_jobs.settings import background_job_settings

logger = logging.getLogger(__name__)


class ArqProducer(Protocol):
    """
    告诉类型检查器：传进来的对象只要具有 enqueue_job() 方法，就可以被 Dispatcher 使用。
    设置它的目的只是为了方便单元测试
    """

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class OutboxRoute:
    """一个 Outbox Topic 到 ARQ 函数与队列的固定映射。"""

    function: str # 收到任务用哪个函数处理
    queue_name: str # worker 监听哪个队列


class OutboxDispatcher:
    """轮询持久化事件，并用稳定 Job ID 投递。"""

    def __init__(self, routes: dict[str, OutboxRoute]) -> None:
        self._routes = routes

    async def dispatch_once(self, redis: ArqProducer) -> int:
        """发布一批事件；单条失败不阻塞同批其他事件。"""
        cutoff = (
            datetime.now(UTC)
            - timedelta(
                seconds=background_job_settings.background_outbox_publish_lease_seconds
            )
        ).isoformat()
        topics = tuple(self._routes)
        async with database_module.db.session() as session:
            repository = OutboxRepository(session)
            await repository.requeue_stale_published(
                before=cutoff,
                topics=topics,
            )
            rows = await repository.list_pending(
                limit=background_job_settings.background_outbox_batch_size,
                topics=topics,
            )
            await session.commit()

        published = 0
        for row in rows:
            route = self._routes.get(row.topic)
            if route is None:
                await self._record_failure(row.id, f"unknown outbox topic: {row.topic}")
                continue
            try:
                await redis.enqueue_job(
                    route.function,
                    row.id,
                    dict(row.payload),
                    _job_id=row.dedupe_key,
                    _queue_name=route.queue_name,
                )
            except Exception as exc:
                logger.warning(
                    "Outbox publish failed: id=%s topic=%s",
                    row.id, # 为了执行结束后把这条 Outbox 标记为 processed
                    row.topic,
                    exc_info=True,
                )
                await self._record_failure(row.id, str(exc))
                continue
            async with database_module.db.session() as session:
                if await OutboxRepository(session).mark_published(row.id):
                    published += 1
                await session.commit()
        return published

    @staticmethod
    async def _record_failure(outbox_id: int, error: str) -> None:
        async with database_module.db.session() as session:
            await OutboxRepository(session).mark_publish_failed(outbox_id, error)
            await session.commit()

    async def run(self, redis: ArqProducer, stop: asyncio.Event) -> None:
        """持续投递，错误只记日志并进入下一轮。"""
        while not stop.is_set():
            try:
                await self.dispatch_once(redis)
            except Exception:
                logger.exception("Outbox dispatch iteration failed")
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=background_job_settings.background_outbox_poll_seconds,
                )
            except TimeoutError:
                pass
