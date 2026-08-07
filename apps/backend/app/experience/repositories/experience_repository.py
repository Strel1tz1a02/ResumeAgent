"""已持久化经历记录的查询与修改。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import String, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EvidenceItem, ExperienceEvidence, ExperienceItem

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


def ordered_evidence_ids(item: ExperienceItem) -> list[int]:
    """从关系集合提取兼容 API 使用的有序 Evidence ID。"""
    return [link.evidence_id for link in item.evidence_links]


class ExperienceRepository:
    """使用调用方持有的共享事务访问经历记录。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, item: ExperienceItem) -> ExperienceItem:
        """保存经历并回填生成的标识符，但不自行提交。"""
        item.evidence_links = []
        self._session.add(item)
        await self._session.flush()
        return item

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
        item.updated_at = _updated_at()
        await self._session.flush()
        return item

    async def set_evidence_ids(
        self, experience_id: int, evidence_ids: list[int]
    ) -> ExperienceItem:
        """用关系表替换有序证据归属。"""
        item = await self.get(experience_id)
        if item is None:
            raise ValueError(f"experience {experience_id} does not exist")
        await self._replace_evidence_links(experience_id, evidence_ids)
        item.updated_at = _updated_at()
        await self._session.flush()
        await self._session.refresh(item, attribute_names=["evidence_links"])
        return item

    async def _replace_evidence_links(
        self, experience_id: int, evidence_ids: list[int]
    ) -> None:
        """依赖外键和唯一约束写入归属，写入前仅生成清晰错误。"""
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_ids must not contain duplicates")
        if evidence_ids:
            found_ids = set(
                await self._session.scalars(
                    select(EvidenceItem.id).where(EvidenceItem.id.in_(evidence_ids))
                )
            )
            missing_ids = set(evidence_ids) - found_ids
            if missing_ids:
                raise ValueError(f"evidence does not exist: {sorted(missing_ids)}")
            owned = list(
                await self._session.scalars(
                    select(ExperienceEvidence).where(
                        ExperienceEvidence.evidence_id.in_(evidence_ids),
                        ExperienceEvidence.experience_id != experience_id,
                    )
                )
            )
            if owned:
                owners = sorted({link.experience_id for link in owned})
                raise ValueError(
                    f"evidence already belongs to experiences: {owners}"
                )
        await self._session.execute(
            delete(ExperienceEvidence).where(
                ExperienceEvidence.experience_id == experience_id
            )
        )
        self._session.add_all(
            ExperienceEvidence(
                experience_id=experience_id,
                evidence_id=evidence_id,
                position=position,
            )
            for position, evidence_id in enumerate(evidence_ids)
        )

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
        item.updated_at = _updated_at()
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
        item.updated_at = _updated_at()
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
