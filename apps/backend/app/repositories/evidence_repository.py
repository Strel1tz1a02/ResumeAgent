"""Queries and mutations for experience evidence records."""

from datetime import datetime, timezone
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EvidenceItem, ExperienceItem

_EVIDENCE_FIELDS = frozenset({"action", "result", "metrics"})
logger = logging.getLogger(__name__)


def _updated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvidenceRepository:
    """Access evidence rows and derive ownership from experience evidence IDs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, item: EvidenceItem) -> EvidenceItem:
        """Store evidence and populate its generated identifier without committing."""
        self._session.add(item)
        await self._session.flush()
        return item

    async def get(self, evidence_id: int) -> EvidenceItem | None:
        """Return an evidence row if it exists."""
        return await self._session.get(EvidenceItem, evidence_id)

    async def get_many_ordered(self, evidence_ids: list[int]) -> list[EvidenceItem]:
        """Expand evidence IDs while preserving the caller's stored order."""
        if not evidence_ids:
            return []
        rows = await self._session.scalars(
            select(EvidenceItem).where(EvidenceItem.id.in_(evidence_ids))
        )
        by_id = {row.id: row for row in rows}
        missing_ids = sorted(set(evidence_ids) - set(by_id))
        if missing_ids:
            logger.warning("Missing evidence IDs while expanding references: %s", missing_ids)
        return [by_id[evidence_id] for evidence_id in evidence_ids if evidence_id in by_id]

    async def update_fields(self, evidence_id: int, fields: dict[str, Any]) -> EvidenceItem:
        """Apply known ORM fields to evidence without committing."""
        item = await self.get(evidence_id)
        if item is None:
            raise ValueError(f"evidence {evidence_id} does not exist")
        unknown = set(fields) - _EVIDENCE_FIELDS
        if unknown:
            raise ValueError(f"unsupported evidence fields: {sorted(unknown)}")
        for name, value in fields.items():
            setattr(item, name, value)
        item.updated_at = _updated_at()
        await self._session.flush()
        return item

    async def delete(self, evidence_id: int) -> bool:
        """Delete unowned evidence; referenced evidence must be detached first."""
        item = await self.get(evidence_id)
        if item is None:
            return False
        owner_id = await self.find_owner_experience_id(evidence_id)
        if owner_id is not None:
            raise ValueError(f"evidence {evidence_id} belongs to experience {owner_id}")
        await self._session.delete(item)
        await self._session.flush()
        return True

    async def find_owner_experience_id(self, evidence_id: int) -> int | None:
        """Return the sole owning experience ID, detecting corrupted shared ownership."""
        experiences = await self._session.scalars(select(ExperienceItem))
        owners = [
            item.experience_id
            for item in experiences
            if evidence_id in (item.evidence_ids or [])
        ]
        if len(owners) > 1:
            raise ValueError(
                f"evidence {evidence_id} belongs to multiple experiences: {owners}"
            )
        return owners[0] if owners else None
