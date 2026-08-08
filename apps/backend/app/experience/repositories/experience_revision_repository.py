"""经历模块统一 revision 的数据库原子 CAS。"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.experience.models import ExperienceRevision, utcnow_iso

RevisionScope = Literal["unit", "collection"]


class RevisionConflictError(ValueError):
    """调用方提交的 revision 已经过期。"""


class ExperienceRevisionRepository:
    """统一创建、读取和原子推进数据单元与集合 revision。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        experience_id: int,
        scope: RevisionScope,
        unit_key: str,
        *,
        ref_id: int = 0,
        revision: int = 0,
    ) -> ExperienceRevision:
        row = ExperienceRevision(
            experience_id=experience_id,
            scope=scope,
            unit_key=unit_key,
            ref_id=ref_id,
            revision=revision,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(
        self,
        experience_id: int,
        scope: RevisionScope,
        unit_key: str,
        *,
        ref_id: int = 0,
    ) -> ExperienceRevision | None:
        return await self._session.scalar(
            select(ExperienceRevision).where(
                ExperienceRevision.experience_id == experience_id,
                ExperienceRevision.scope == scope,
                ExperienceRevision.unit_key == unit_key,
                ExperienceRevision.ref_id == ref_id,
            )
        )

    async def claim(
        self,
        experience_id: int,
        scope: RevisionScope,
        unit_key: str,
        expected_revision: int,
        *,
        ref_id: int = 0,
    ) -> int:
        """用单条 UPDATE 比较并推进 revision，返回推进后的值。"""
        result = await self._session.execute(
            update(ExperienceRevision)
            .where(
                ExperienceRevision.experience_id == experience_id,
                ExperienceRevision.scope == scope,
                ExperienceRevision.unit_key == unit_key,
                ExperienceRevision.ref_id == ref_id,
                ExperienceRevision.revision == expected_revision,
            )
            .values(
                revision=ExperienceRevision.revision + 1,
                updated_at=utcnow_iso(),
            )
        )
        if result.rowcount != 1:
            raise RevisionConflictError(
                f"stale revision: experience={experience_id} scope={scope} "
                f"unit={unit_key} ref_id={ref_id} expected={expected_revision}"
            )
        await self._session.flush()
        return expected_revision + 1

    async def verify(
        self,
        experience_id: int,
        scope: RevisionScope,
        unit_key: str,
        expected_revision: int,
        *,
        ref_id: int = 0,
    ) -> None:
        """用行级 CAS 固定当前 revision，但不推进未发生变化的版本。"""
        result = await self._session.execute(
            update(ExperienceRevision)
            .where(
                ExperienceRevision.experience_id == experience_id,
                ExperienceRevision.scope == scope,
                ExperienceRevision.unit_key == unit_key,
                ExperienceRevision.ref_id == ref_id,
                ExperienceRevision.revision == expected_revision,
            )
            .values(revision=ExperienceRevision.revision)
        )
        if result.rowcount != 1:
            raise RevisionConflictError(
                f"stale revision: experience={experience_id} scope={scope} "
                f"unit={unit_key} ref_id={ref_id} expected={expected_revision}"
            )
        await self._session.flush()

    async def current(
        self,
        experience_id: int,
        scope: RevisionScope,
        unit_key: str,
        *,
        ref_id: int = 0,
    ) -> int:
        row = await self.get(
            experience_id, scope, unit_key, ref_id=ref_id
        )
        if row is None:
            raise RuntimeError(
                f"missing revision: experience={experience_id} scope={scope} "
                f"unit={unit_key} ref_id={ref_id}"
            )
        return row.revision
