"""已持久化经历记录的查询与修改。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import String, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EvidenceItem, ExperienceItem

ExperienceStatusFilter = Literal["active", "draft", "ready", "archived"]
ExperienceSort = Literal["updated_at_desc", "created_at_desc", "created_at_asc"]
ExperienceLifecycleStatus = Literal["draft", "ready", "archived"]

_EXPERIENCE_FIELDS = frozenset(
    {
        "kind",
        "title",
        "organization",
        "role",
        "location",
        "start_date",
        "end_date",
        "is_current",
        "background",
        "technologies",
        "tags",
        "notes",
    }
)


def _updated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_updated_at(observed_updated_at: str) -> str:
    """生成严格晚于刚读取版本的 UTC 审计时间戳。"""
    observed = datetime.fromisoformat(observed_updated_at)
    current = datetime.fromisoformat(_updated_at())
    if current > observed:
        return current.isoformat()
    return (observed + timedelta(microseconds=1)).isoformat()


class ExperienceStaleWriteError(ValueError):
    """经历读取后被其他事务修改时抛出。"""


class ExperienceRepository:
    """使用调用方持有的共享事务访问经历记录。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, item: ExperienceItem) -> ExperienceItem:
        """保存经历并回填生成的标识符，但不自行提交。"""
        self._session.add(item)
        await self._session.flush()
        return item

    async def acquire_ownership_write_lock(self) -> None:
        """在调用方事务中串行执行 JSON 证据所有权校验。

        SQLite 没有行级锁，JSON 引用也没有外键约束，因此必须在读取任何
        所有权信息前执行 `BEGIN IMMEDIATE`。其他数据库则使用加锁查询作为
        最接近的等价实现。
        """
        if self._session.in_transaction():
            raise RuntimeError("ownership write lock must be acquired before any database operation")
        connection = await self._session.connection()
        if connection.dialect.name == "sqlite":
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            return
        await self._session.execute(
            select(ExperienceItem.experience_id).limit(1).with_for_update()
        )

    async def get(self, experience_id: int) -> ExperienceItem | None:
        """返回一条经历，不限制其生命周期状态。"""
        return await self._session.get(ExperienceItem, experience_id)

    async def list(
        self,
        *,
        q: str | None = None,
        kind: str | None = None,
        status: ExperienceStatusFilter = "active",
        sort: ExperienceSort = "updated_at_desc",
    ) -> list[ExperienceItem]:
        """按约定的活动状态、筛选、搜索和排序契约返回经历。"""
        statement = select(ExperienceItem)
        if status == "active":
            statement = statement.where(ExperienceItem.status.in_(("draft", "ready")))
        elif status in {"draft", "ready", "archived"}:
            statement = statement.where(ExperienceItem.status == status)
        else:
            raise ValueError(f"unsupported experience status filter: {status}")

        if kind is not None:
            statement = statement.where(ExperienceItem.kind == kind)

        if q and q.strip():
            pattern = f"%{q.strip().lower()}%"
            statement = statement.where(
                or_(
                    func.lower(ExperienceItem.title).like(pattern),
                    func.lower(ExperienceItem.organization).like(pattern),
                    func.lower(ExperienceItem.role).like(pattern),
                    func.lower(ExperienceItem.background).like(pattern),
                    func.lower(ExperienceItem.technologies.cast(String)).like(pattern),
                    func.lower(ExperienceItem.tags.cast(String)).like(pattern),
                )
            )

        if sort == "updated_at_desc":
            statement = statement.order_by(
                ExperienceItem.updated_at.desc(), ExperienceItem.experience_id.desc()
            )
        elif sort == "created_at_desc":
            statement = statement.order_by(
                ExperienceItem.created_at.desc(), ExperienceItem.experience_id.desc()
            )
        elif sort == "created_at_asc":
            statement = statement.order_by(
                ExperienceItem.created_at.asc(), ExperienceItem.experience_id.asc()
            )
        else:
            raise ValueError(f"unsupported experience sort: {sort}")

        return list((await self._session.scalars(statement)).all())

    async def update_fields(
        self, experience_id: int, fields: dict[str, Any]
    ) -> ExperienceItem:
        """将已知 ORM 字段应用到一条经历，但不自行提交。"""
        item = await self.get(experience_id)
        if item is None:
            raise ValueError(f"experience {experience_id} does not exist")
        unknown = set(fields) - _EXPERIENCE_FIELDS
        if unknown:
            raise ValueError(f"unsupported experience fields: {sorted(unknown)}")
        for name, value in fields.items():
            setattr(item, name, value)
        item.updated_at = _next_updated_at(item.updated_at)
        await self._session.flush()
        return item

    async def update_fields_if_current(
        self,
        experience_id: int,
        observed_updated_at: str,
        fields: dict[str, Any],
    ) -> ExperienceItem:
        """仅当调用方读取的版本仍为当前版本时更新可编辑字段。"""
        unknown = set(fields) - _EXPERIENCE_FIELDS
        if unknown:
            raise ValueError(f"unsupported experience fields: {sorted(unknown)}")

        result = await self._session.execute(
            update(ExperienceItem)
            .where(
                ExperienceItem.experience_id == experience_id,
                ExperienceItem.updated_at == observed_updated_at,
            )
            .values(**fields, updated_at=_next_updated_at(observed_updated_at))
        )
        if result.rowcount != 1:
            raise ExperienceStaleWriteError(
                f"stale experience update for {experience_id}: the record has changed since it was read"
            )

        await self._session.flush()
        item = await self.get(experience_id)
        if item is None:
            raise ExperienceStaleWriteError(
                f"stale experience update for {experience_id}: the record has changed since it was read"
            )
        await self._session.refresh(item)
        return item

    async def set_evidence_ids(
        self, experience_id: int, evidence_ids: list[int]
    ) -> ExperienceItem:
        """设置有序证据引用，并强制证据只能属于一条经历。"""
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_ids must not contain duplicates")
        item = await self.get(experience_id)
        if item is None:
            raise ValueError(f"experience {experience_id} does not exist")

        evidence_rows = await self._session.scalars(
            select(EvidenceItem).where(EvidenceItem.id.in_(evidence_ids))
        )
        found_ids = {row.id for row in evidence_rows}
        missing_ids = set(evidence_ids) - found_ids
        if missing_ids:
            raise ValueError(f"evidence does not exist: {sorted(missing_ids)}")

        all_experiences = await self._session.scalars(select(ExperienceItem))
        for other in all_experiences:
            if other.experience_id == experience_id:
                continue
            shared_ids = set(evidence_ids).intersection(other.evidence_ids or [])
            if shared_ids:
                raise ValueError(
                    f"evidence {sorted(shared_ids)} already belongs to experience "
                    f"{other.experience_id}"
                )

        item.evidence_ids = list(evidence_ids)
        item.updated_at = _next_updated_at(item.updated_at)
        await self._session.flush()
        return item

    async def set_evidence_ids_if_current(
        self,
        experience_id: int,
        observed_updated_at: str,
        evidence_ids: list[int],
    ) -> ExperienceItem:
        """仅当已读取版本仍为当前版本时原子替换证据引用。"""
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_ids must not contain duplicates")
        item = await self.get(experience_id)
        if item is None:
            raise ValueError(f"experience {experience_id} does not exist")

        evidence_rows = await self._session.scalars(
            select(EvidenceItem).where(EvidenceItem.id.in_(evidence_ids))
        )
        found_ids = {row.id for row in evidence_rows}
        missing_ids = set(evidence_ids) - found_ids
        if missing_ids:
            raise ValueError(f"evidence does not exist: {sorted(missing_ids)}")

        all_experiences = await self._session.scalars(select(ExperienceItem))
        for other in all_experiences:
            if other.experience_id == experience_id:
                continue
            shared_ids = set(evidence_ids).intersection(other.evidence_ids or [])
            if shared_ids:
                raise ValueError(
                    f"evidence {sorted(shared_ids)} already belongs to experience "
                    f"{other.experience_id}"
                )

        result = await self._session.execute(
            update(ExperienceItem)
            .where(
                ExperienceItem.experience_id == experience_id,
                ExperienceItem.updated_at == observed_updated_at,
            )
            .values(
                evidence_ids=list(evidence_ids),
                updated_at=_next_updated_at(observed_updated_at),
            )
        )
        if result.rowcount != 1:
            raise ExperienceStaleWriteError(
                f"stale experience update for {experience_id}: the record has changed since it was read"
            )
        await self._session.flush()
        await self._session.refresh(item)
        return item

    async def set_completeness(
        self, experience_id: int, completeness: int
    ) -> ExperienceItem:
        """设置服务端计算的完整度，不开放通用审计字段写入。"""
        if isinstance(completeness, bool) or not isinstance(completeness, int):
            raise ValueError("completeness must be an integer from 0 to 100")
        if not 0 <= completeness <= 100:
            raise ValueError("completeness must be an integer from 0 to 100")
        item = await self.get(experience_id)
        if item is None:
            raise ValueError(f"experience {experience_id} does not exist")
        item.completeness = completeness
        item.updated_at = _next_updated_at(item.updated_at)
        await self._session.flush()
        return item

    async def set_status(
        self,
        experience_id: int,
        status: ExperienceLifecycleStatus,
    ) -> ExperienceItem:
        """应用有效的生命周期状态，并保持归档时间一致。"""
        if status not in {"draft", "ready", "archived"}:
            raise ValueError(f"unsupported experience status: {status}")
        item = await self.get(experience_id)
        if item is None:
            raise ValueError(f"experience {experience_id} does not exist")
        item.status = status
        item.archived_at = _updated_at() if status == "archived" else None
        item.updated_at = _next_updated_at(item.updated_at)
        await self._session.flush()
        return item

    async def delete(self, experience_id: int) -> bool:
        """移除一条经历记录，但不自行提交或删除其证据。"""
        item = await self.get(experience_id)
        if item is None:
            return False
        await self._session.delete(item)
        await self._session.flush()
        return True
