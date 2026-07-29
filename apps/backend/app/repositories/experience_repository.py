"""Queries and mutations for persisted experience records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import String, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EvidenceItem, ExperienceItem

ExperienceStatusFilter = Literal["active", "draft", "ready", "archived"]
ExperienceSort = Literal["updated_at_desc", "created_at_desc", "created_at_asc"]

_EXPERIENCE_FIELDS = frozenset(ExperienceItem.__table__.columns.keys()) - {
    "evidence_ids",
    "experience_id",
}


def _updated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExperienceRepository:
    """Access experience rows using a caller-owned shared transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, item: ExperienceItem) -> ExperienceItem:
        """Store an experience and populate its generated identifier without committing."""
        self._session.add(item)
        await self._session.flush()
        return item

    async def get(self, experience_id: int) -> ExperienceItem | None:
        """Return one experience regardless of lifecycle status."""
        return await self._session.get(ExperienceItem, experience_id)

    async def list(
        self,
        *,
        q: str | None = None,
        kind: str | None = None,
        status: ExperienceStatusFilter = "active",
        sort: ExperienceSort = "updated_at_desc",
    ) -> list[ExperienceItem]:
        """Return experiences using the approved active, filter, search, and sort contract."""
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
                    func.lower(ExperienceItem.raw_input).like(pattern),
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
        """Apply known ORM fields to one experience without committing."""
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
        """Set ordered evidence references while enforcing single-experience ownership."""
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
        item.updated_at = _updated_at()
        await self._session.flush()
        return item

    async def delete(self, experience_id: int) -> bool:
        """Remove one experience row without committing or deleting its evidence."""
        item = await self.get(experience_id)
        if item is None:
            return False
        await self._session.delete(item)
        await self._session.flush()
        return True
