"""使用调用方事务维护后台任务 Outbox。"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.background_jobs.models import BackgroundJobOutbox, utcnow_iso


class OutboxRepository:
    """提供幂等写入、发布租约与处理完成状态。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(
        self,
        *,
        topic: str,
        dedupe_key: str,
        payload: dict[str, Any],
    ) -> BackgroundJobOutbox:
        """在当前事务中幂等写入一个待发布事件。"""
        now = utcnow_iso()
        statement = (
            sqlite_insert(BackgroundJobOutbox)
            .values(
                topic=topic,
                dedupe_key=dedupe_key,
                payload=payload,
                status="pending",
                publish_attempts=0,
                available_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
        )
        await self._session.execute(statement)
        await self._session.flush()
        result = await self._session.execute(
            select(BackgroundJobOutbox).where(
                BackgroundJobOutbox.dedupe_key == dedupe_key
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise RuntimeError("outbox event was not persisted")
        return row

    async def list_pending(
        self,
        *,
        limit: int,
        now: str | None = None,
        topics: Collection[str] | None = None,
    ) -> list[BackgroundJobOutbox]:
        """按创建顺序读取当前可发布的事件。"""
        filters = [
            BackgroundJobOutbox.status == "pending",
            BackgroundJobOutbox.available_at <= (now or utcnow_iso()),
        ]
        if topics is not None:
            filters.append(BackgroundJobOutbox.topic.in_(tuple(topics)))
        result = await self._session.execute(
            select(BackgroundJobOutbox)
            .where(*filters)
            .order_by(BackgroundJobOutbox.id)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_published(self, outbox_id: int) -> bool:
        """记录 Redis 已接受事件或已存在同一幂等 Job。"""
        now = utcnow_iso()
        result = await self._session.execute(
            update(BackgroundJobOutbox)
            .where(
                BackgroundJobOutbox.id == outbox_id,
                BackgroundJobOutbox.status == "pending",
            )
            .values(
                status="published",
                published_at=now,
                updated_at=now,
                last_error=None,
            )
        )
        await self._session.flush()
        return result.rowcount == 1

    async def mark_publish_failed(self, outbox_id: int, error: str) -> bool:
        """保留事件并以短指数退避等待再次投递。"""
        row = await self._session.get(BackgroundJobOutbox, outbox_id)
        if row is None or row.status != "pending":
            return False
        attempts = row.publish_attempts + 1
        delay_seconds = min(30.0, float(2 ** min(attempts - 1, 5)))
        row.publish_attempts = attempts
        row.available_at = (
            datetime.now(UTC) + timedelta(seconds=delay_seconds)
        ).isoformat()
        row.last_error = error[:2000]
        row.updated_at = utcnow_iso()
        await self._session.flush()
        return True

    async def mark_processed(self, outbox_id: int) -> bool:
        """把压缩完成或已跳过的事件标记为终态。"""
        now = utcnow_iso()
        result = await self._session.execute(
            update(BackgroundJobOutbox)
            .where(
                BackgroundJobOutbox.id == outbox_id,
                BackgroundJobOutbox.status.in_(("pending", "published")),
            )
            .values(
                status="processed",
                processed_at=now,
                updated_at=now,
                last_error=None,
            )
        )
        await self._session.flush()
        return result.rowcount == 1

    async def requeue_stale_published(
        self,
        *,
        before: str,
        topics: Collection[str] | None = None,
    ) -> int:
        """回收发布后没有得到 Worker 确认的事件。"""
        now = utcnow_iso()
        filters = [
            BackgroundJobOutbox.status == "published",
            BackgroundJobOutbox.published_at < before,
        ]
        if topics is not None:
            filters.append(BackgroundJobOutbox.topic.in_(tuple(topics)))
        result = await self._session.execute(
            update(BackgroundJobOutbox)
            .where(*filters)
            .values(
                status="pending",
                available_at=now,
                published_at=None,
                updated_at=now,
            )
        )
        await self._session.flush()
        return int(result.rowcount or 0)

    async def get(self, outbox_id: int) -> BackgroundJobOutbox | None:
        """按 ID 读取事件。"""
        return await self._session.get(BackgroundJobOutbox, outbox_id)
