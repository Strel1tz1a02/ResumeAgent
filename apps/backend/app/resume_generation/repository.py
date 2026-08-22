"""简历生成运行的事务内仓储。"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.resume_generation.models import ResumeGenerationRun


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class ResumeGenerationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        run_id: str,
        jd_information_id: int,
        request_json: dict[str, Any],
    ) -> ResumeGenerationRun:
        row = ResumeGenerationRun(
            run_id=run_id,
            jd_information_id=jd_information_id,
            request_json=request_json,
            status="running",
            artifact_status="pending",
            created_at=utcnow_iso(),
            updated_at=utcnow_iso(),
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, run_id: str) -> ResumeGenerationRun | None:
        return await self._session.get(ResumeGenerationRun, run_id)

    async def transition(
        self,
        run_id: str,
        *,
        from_statuses: Collection[str],
        to_status: str,
        error_code: str | None = None,
    ) -> bool:
        """以 CAS 方式持久化通用 Run 状态。"""
        result = await self._session.execute(
            update(ResumeGenerationRun)
            .where(
                ResumeGenerationRun.run_id == run_id,
                ResumeGenerationRun.status.in_(tuple(from_statuses)),
            )
            .values(status=to_status, error=error_code, updated_at=utcnow_iso())
        )
        await self._session.flush()
        return result.rowcount == 1

    async def update(self, row: ResumeGenerationRun, **fields: Any) -> None:
        for key, value in fields.items():
            if not hasattr(row, key):
                raise ValueError(f"unsupported resume generation run field: {key}")
            setattr(row, key, value)
        row.updated_at = utcnow_iso()
        await self._session.flush()
