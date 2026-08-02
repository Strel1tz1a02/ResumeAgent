"""经历证据记录的查询与修改。"""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EvidenceItem, ExperienceItem

_EVIDENCE_FIELDS = frozenset({"action", "result", "metrics"})
logger = logging.getLogger(__name__)


def _updated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_updated_at(observed_updated_at: str) -> str:
    """生成严格晚于已读取版本的证据审计时间戳。"""
    observed = datetime.fromisoformat(observed_updated_at)
    current = datetime.fromisoformat(_updated_at())
    if current > observed:
        return current.isoformat()
    return (observed + timedelta(microseconds=1)).isoformat()


class EvidenceRepository:
    """访问证据记录，并根据经历的证据 ID 推导所有权。"""

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

    async def get_many_ordered(self, evidence_ids: list[int]) -> list[EvidenceItem]:
        """展开证据 ID，并保持调用方保存的顺序。"""
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
        """将已知 ORM 字段应用到证据，但不自行提交。"""
        item = await self.get(evidence_id)
        if item is None:
            raise ValueError(f"evidence {evidence_id} does not exist")
        unknown = set(fields) - _EVIDENCE_FIELDS
        if unknown:
            raise ValueError(f"unsupported evidence fields: {sorted(unknown)}")
        for name, value in fields.items():
            setattr(item, name, value)
        item.updated_at = _next_updated_at(item.updated_at)
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
        """返回唯一所属经历 ID，并检测异常的共享所有权。"""
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
