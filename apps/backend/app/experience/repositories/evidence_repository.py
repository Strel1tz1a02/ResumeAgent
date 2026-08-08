"""经历证据记录的查询与修改。"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.experience.models import EvidenceItem, ExperienceEvidence

_EVIDENCE_FIELDS = frozenset({"action", "result", "metrics"})


def _updated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvidenceRepository:
    """访问 EvidenceItem 及其关系表归属。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, item: EvidenceItem) -> EvidenceItem:
        """保存证据并回填生成的标识符，但不自行提交。"""
        self._session.add(item)
        await self._session.flush()
        return item

    async def get(self, evidence_id: int) -> EvidenceItem | None:
        """证据存在时返回对应记录。"""
        return await self._session.get(EvidenceItem, evidence_id)

    async def list_for_experience(self, experience_id: int) -> list[EvidenceItem]:
        """由关系表按 position 返回一条经历拥有的证据。"""
        rows = await self._session.scalars(
            select(EvidenceItem)
            .join(ExperienceEvidence)
            .where(ExperienceEvidence.experience_id == experience_id)
            .order_by(ExperienceEvidence.position)
        )
        return list(rows)

    async def get_for_experience(
        self, experience_id: int, evidence_id: int
    ) -> EvidenceItem | None:
        """仅在关系表确认归属时返回 EvidenceItem。"""
        return await self._session.scalar(
            select(EvidenceItem)
            .join(ExperienceEvidence)
            .where(
                ExperienceEvidence.experience_id == experience_id,
                ExperienceEvidence.evidence_id == evidence_id,
            )
        )

    async def update_fields(self, evidence_id: int, fields: dict[str, Any]) -> EvidenceItem:
        """将已知 ORM 字段应用到证据，但不自行提交。"""
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
        """删除无主证据；仍被引用的证据必须先解除关联。"""
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
        """通过 evidence_id 唯一约束返回所属经历。"""
        return await self._session.scalar(
            select(ExperienceEvidence.experience_id).where(
                ExperienceEvidence.evidence_id == evidence_id
            )
        )
