"""Queries and mutations for persisted experience records."""

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
    """Generate a UTC audit timestamp strictly later than the version just observed."""
    observed = datetime.fromisoformat(observed_updated_at)
    current = datetime.fromisoformat(_updated_at())
    if current > observed:
        return current.isoformat()
    return (observed + timedelta(microseconds=1)).isoformat()


class ExperienceStaleWriteError(ValueError):
    """Raised when another transaction has changed an experience since it was read."""


class ExperienceRepository:
    """Access experience rows using a caller-owned shared transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, item: ExperienceItem) -> ExperienceItem:
        """Store an experience and populate its generated identifier without committing."""
        self._session.add(item)
        await self._session.flush()
        return item

    async def acquire_ownership_write_lock(self) -> None:
        """Serialize JSON evidence ownership checks for the caller-owned transaction.

        SQLite has no row-level locking and JSON references have no foreign-key
        constraint, so its `BEGIN IMMEDIATE` must happen before any ownership
        read. Other databases use a locking select as the closest equivalent.
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
        """Apply known ORM fields to one experience without committing."""
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
        """Update editable fields only while the caller's observed version is current."""
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
        item.updated_at = _next_updated_at(item.updated_at)
        await self._session.flush()
        return item

    async def set_evidence_ids_if_current(
        self,
        experience_id: int,
        observed_updated_at: str,
        evidence_ids: list[int],
    ) -> ExperienceItem:
        """Atomically replace evidence references only while the observed version is current."""
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
        """Set a server-computed completeness score without exposing generic audit writes."""
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
        """Apply a valid lifecycle state and keep its archive timestamp consistent."""
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
        """Remove one experience row without committing or deleting its evidence."""
        item = await self.get(experience_id)
        if item is None:
            return False
        await self._session.delete(item)
        await self._session.flush()
        return True
