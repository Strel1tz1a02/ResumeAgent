"""AI 审批通过后的经历字段原子写入。"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config_cache import get_content_language
from app.models import EvidenceItem, ExperienceItem
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.experience_repository import ExperienceRepository
from app.schemas.experiences import ExperienceDetail, ExperienceUpdate
from app.services.experience_completeness_service import (
    READY_COMPLETENESS_THRESHOLD,
    calculate_completeness,
)
from app.services.experience_field_service import ExperienceFieldService
from app.services.experience_fields import normalize_field_value
from app.services.experience_service import ExperienceService


class ExperienceAiMutationService:
    """在字段 revision 和当前值仍匹配时执行一次 AI 提案。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._experiences = ExperienceRepository(session)
        self._evidence = EvidenceRepository(session)
        self._fields = ExperienceFieldService(session)

    async def apply_field(
        self,
        experience_id: int,
        key: str,
        proposed_value: Any,
        *,
        expected_revision: int,
        expected_value: Any,
    ) -> dict[str, Any]:
        """校验 guard 后只覆盖一个 ExperienceItem 字段。"""
        await self._experiences.acquire_ownership_write_lock()
        item = await self._experience(experience_id)
        snapshot = await self._fields.snapshot(experience_id, key, None)
        if item.status == "archived" or (
            snapshot.revision != expected_revision
            or snapshot.normalized_value != expected_value
        ):
            return await self._invalidated(item, snapshot)
        try:
            request = ExperienceUpdate.model_validate({key: proposed_value})
        except ValidationError as error:
            raise ValueError("invalid proposed experience value") from error
        value = request.model_dump(exclude_unset=True)[key]
        ExperienceService._validate_merged_dates(item, {key: value})
        if normalize_field_value(key, value) == snapshot.normalized_value:
            return {"outcome": "no_change", "operation": "field_overwrite"}
        updated = await self._experiences.update_fields_if_current(
            experience_id, item.updated_at, {key: value}
        )
        await self._fields.advance_experience_fields(updated, {key})
        updated = await self._refresh_completeness(updated)
        detail = await ExperienceService(self._session)._detail(updated)
        current = await self._fields.snapshot(experience_id, key, None)
        await self._session.commit()
        return self._applied("field_overwrite", current, detail)

    async def apply_evidence(
        self,
        experience_id: int,
        evidence_id: int,
        key: str,
        value: Any,
        *,
        expected_revision: int,
        expected_value: Any,
    ) -> dict[str, Any]:
        """按 ID 局部修改一个 Evidence 字段，其他字段保持原值。"""
        await self._experiences.acquire_ownership_write_lock()
        item = await self._experience(experience_id)
        if evidence_id not in (item.evidence_ids or []):
            return await self._missing_evidence(item, evidence_id)
        snapshot = await self._fields.snapshot(experience_id, key, evidence_id)
        if item.status == "archived" or (
            snapshot.revision != expected_revision
            or snapshot.normalized_value != expected_value
        ):
            return await self._invalidated(item, snapshot, operation="evidence_update")
        normalized = normalize_field_value(key, value)
        if key == "action" and not normalized:
            raise ValueError("evidence action cannot be blank")
        if normalized == snapshot.normalized_value:
            return {"outcome": "no_change", "operation": "evidence_update"}
        evidence = await self._evidence.update_fields(evidence_id, {key: normalized})
        await self._fields.advance_evidence_fields(experience_id, evidence)
        updated = await self._refresh_completeness(item)
        detail = await ExperienceService(self._session)._detail(updated)
        current = await self._fields.snapshot(experience_id, key, evidence_id)
        await self._session.commit()
        return self._applied("evidence_update", current, detail)

    async def append_evidence(
        self,
        experience_id: int,
        item_payload: dict[str, Any],
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        """集合 revision 未变化时创建 Evidence 并追加到末尾。"""
        await self._experiences.acquire_ownership_write_lock()
        item = await self._experience(experience_id)
        collection = await self._fields.snapshot(experience_id, "evidence_new", None)
        if item.status == "archived" or collection.revision != expected_revision:
            return await self._invalidated(
                item, collection, operation="evidence_append"
            )
        evidence = await self._evidence.create(EvidenceItem(**item_payload))
        updated = await self._experiences.set_evidence_ids_if_current(
            experience_id,
            item.updated_at,
            [*(item.evidence_ids or []), evidence.id],
        )
        await self._fields.initialize_evidence(experience_id, evidence)
        await self._fields.advance_collection(updated)
        updated = await self._refresh_completeness(updated)
        detail = await ExperienceService(self._session)._detail(updated)
        collection = await self._fields.snapshot(experience_id, "evidence_new", None)
        await self._session.commit()
        return {
            "outcome": "applied",
            "operation": "evidence_append",
            "target": {"key": "evidence_new", "ref_id": None},
            "evidence_id": evidence.id,
            "evidence_ids": list(updated.evidence_ids or []),
            "revision": collection.revision,
            "field_status": collection.status,
            "experience": detail.model_dump(mode="json"),
        }

    async def _experience(self, experience_id: int) -> ExperienceItem:
        item = await self._experiences.get(experience_id)
        if item is None:
            raise ValueError("experience does not exist")
        return item

    async def _missing_evidence(
        self, item: ExperienceItem, evidence_id: int
    ) -> dict[str, Any]:
        """Evidence 已被删除时返回业务失效而不是执行覆盖。"""
        detail = await ExperienceService(self._session)._detail(item)
        await self._session.rollback()
        return {
            "outcome": "invalidated",
            "operation": "evidence_update",
            "target": {"key": "evidence", "ref_id": evidence_id},
            "current_value": None,
            "experience": detail.model_dump(mode="json"),
        }

    async def _refresh_completeness(self, item: ExperienceItem) -> ExperienceItem:
        evidence = await self._evidence.get_many_ordered(item.evidence_ids or [])
        guidance = calculate_completeness(
            item, evidence, language=get_content_language()
        )
        updated = await self._experiences.set_completeness(
            item.experience_id, guidance.completeness
        )
        if updated.status == "ready" and guidance.completeness < READY_COMPLETENESS_THRESHOLD:
            updated = await self._experiences.set_status(item.experience_id, "draft")
        return updated

    async def _invalidated(
        self,
        item: ExperienceItem,
        snapshot: Any,
        *,
        operation: str = "field_overwrite",
    ) -> dict[str, Any]:
        detail = await ExperienceService(self._session)._detail(item)
        await self._session.rollback()
        return {
            "outcome": "invalidated",
            "operation": operation,
            "target": {"key": snapshot.key, "ref_id": snapshot.ref_id},
            "current_value": snapshot.value,
            "revision": snapshot.revision,
            "experience": detail.model_dump(mode="json"),
        }

    @staticmethod
    def _applied(
        operation: str, snapshot: Any, detail: ExperienceDetail
    ) -> dict[str, Any]:
        return {
            "outcome": "applied",
            "operation": operation,
            "target": {"key": snapshot.key, "ref_id": snapshot.ref_id},
            "value": snapshot.value,
            "revision": snapshot.revision,
            "field_status": snapshot.status,
            "experience": detail.model_dump(mode="json"),
        }
