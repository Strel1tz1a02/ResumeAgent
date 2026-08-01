"""经历字段状态与 revision 的事务内 Repository。"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ExperienceFieldState, _utcnow_iso


class ExperienceFieldStateRepository:
    """使用调用方持有的 Session 读取和更新字段并发状态。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_experience(self, experience_id: int) -> list[ExperienceFieldState]:
        """返回一条经历的全部字段状态。"""
        rows = await self._session.scalars(
            select(ExperienceFieldState)
            .where(ExperienceFieldState.experience_id == experience_id)
            .order_by(ExperienceFieldState.id)
        )
        return list(rows.all())

    async def get(
        self, experience_id: int, target_key: str, ref_id: int = 0
    ) -> ExperienceFieldState | None:
        """读取一个严格目标的状态。"""
        return await self._session.scalar(
            select(ExperienceFieldState).where(
                ExperienceFieldState.experience_id == experience_id,
                ExperienceFieldState.target_key == target_key,
                ExperienceFieldState.ref_id == ref_id,
            )
        )

    async def create(
        self,
        experience_id: int,
        target_key: str,
        status: str,
        *,
        ref_id: int = 0,
        revision: int = 0,
    ) -> ExperienceFieldState:
        """创建迁移或领域事务已经证明不存在的状态行。"""
        row = ExperienceFieldState(
            experience_id=experience_id,
            target_key=target_key,
            ref_id=ref_id,
            status=status,
            revision=revision,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def advance(
        self, row: ExperienceFieldState, *, status: str | None = None
    ) -> ExperienceFieldState:
        """将 revision 单调推进一次，并可同步更新状态。"""
        row.revision += 1
        if status is not None:
            row.status = status
        row.updated_at = _utcnow_iso()
        await self._session.flush()
        return row

    async def delete_targets(
        self, experience_id: int, target_keys: Iterable[str], *, ref_id: int
    ) -> None:
        """删除一组属于同一 Evidence 的字段状态。"""
        await self._session.execute(
            delete(ExperienceFieldState).where(
                ExperienceFieldState.experience_id == experience_id,
                ExperienceFieldState.ref_id == ref_id,
                ExperienceFieldState.target_key.in_(tuple(target_keys)),
            )
        )

