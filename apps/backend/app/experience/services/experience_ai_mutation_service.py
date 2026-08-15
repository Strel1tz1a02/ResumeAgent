"""AI 审批通过后的经历字段原子写入。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config_cache import get_content_language
from app.experience.models import EvidenceItem, ExperienceItem
from app.experience.repositories.evidence_repository import EvidenceRepository
from app.experience.repositories.experience_repository import (
    ExperienceRepository,
    ordered_evidence_ids,
)
from app.experience.schemas.evidence_items import EvidenceCreate
from app.experience.schemas.experiences import ExperienceDetail, ExperienceUpdate
from app.experience.services.experience_completeness_service import (
    READY_COMPLETENESS_THRESHOLD,
    calculate_completeness,
)
from app.experience.services.experience_field_service import ExperienceFieldService
from app.experience.services.experience_fields import (
    EXPERIENCE_TARGET_KEYS,
    normalize_field_value,
)
from app.experience.services.experience_service import ExperienceService
from app.resume_generation.indexing import enqueue_experience_index_sync


@dataclass(frozen=True)
class PreparedExperienceChange:
    """Service 校验后交给 Tool 层持久化的审批提案。"""

    proposal_payload: dict[str, Any]
    guard_payload: dict[str, Any]


class ExperienceAiMutationService:
    """在字段 revision 和当前值仍匹配时执行一次 AI 提案。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._experiences = ExperienceRepository(session)
        self._evidence = EvidenceRepository(session)
        self._fields = ExperienceFieldService(session)

    async def prepare_field_change(
        self,
        experience_id: int,
        requested_field: str,
        evidence_id: int | None,
        suggested_content: Any,
        *,
        scope_field: str,
        expected_revision: int,
    ) -> PreparedExperienceChange | dict[str, Any]:
        """校验 ExperienceItem 字段建议并生成审批快照。"""
        if (
            requested_field != scope_field
            or evidence_id is not None
            or requested_field not in EXPERIENCE_TARGET_KEYS
        ):
            return self._immediate("invalid_scope")
        item = await self._experiences.get(experience_id)
        if item is None or item.status == "archived":
            return self._immediate("invalidated")
        snapshot = await self._fields.snapshot(experience_id, requested_field, None)
        if snapshot.revision != expected_revision:
            return self._immediate("invalidated")
        try:
            request = ExperienceUpdate.model_validate(
                {requested_field: suggested_content}
            )
            proposed = request.model_dump(mode="json", exclude_unset=True)[
                requested_field
            ]
            ExperienceService._validate_merged_dates(item, {requested_field: proposed})
        except (ValidationError, ValueError):
            return self._immediate("invalid_value")
        normalized = normalize_field_value(requested_field, proposed)
        if normalized == snapshot.normalized_value:
            return self._immediate("no_change")
        return self._proposal(
            experience_id,
            requested_field,
            None,
            snapshot.value,
            proposed,
            snapshot.revision,
            snapshot.normalized_value,
        )

    async def prepare_evidence_change(
        self,
        experience_id: int,
        requested_field: str,
        evidence_id: int | None,
        suggested_content: Any,
        *,
        scope_field: str,
        expected_revision: int | None,
    ) -> PreparedExperienceChange | dict[str, Any]:
        """校验一个完整 EvidenceItem 的覆盖建议并生成审批快照。"""
        if (
            scope_field != "evidence"
            or requested_field != "evidence"
            or evidence_id is None
        ):
            return self._immediate("invalid_scope")
        item = await self._experiences.get(experience_id)
        evidence = await self._evidence.get_for_experience(experience_id, evidence_id)
        if item is None or item.status == "archived" or evidence is None:
            return self._immediate("invalidated")
        snapshot = await self._fields.snapshot(experience_id, "action", evidence_id)
        if expected_revision is None or snapshot.revision != expected_revision:
            return self._immediate("invalidated")
        try:
            proposed = EvidenceCreate.model_validate(suggested_content).model_dump(
                mode="json"
            )
        except ValidationError:
            return self._immediate("invalid_value")
        current = self._evidence_value(evidence)
        if proposed == current:
            return self._immediate("no_change")
        return self._proposal(
            experience_id,
            "evidence",
            evidence_id,
            current,
            proposed,
            snapshot.revision,
            current,
        )

    async def prepare_evidence_append(
        self,
        experience_id: int,
        requested_field: str,
        evidence_id: int | None,
        suggested_content: Any,
        *,
        scope_field: str,
        expected_revision: int,
    ) -> PreparedExperienceChange | dict[str, Any]:
        """校验新增 Evidence 建议并生成集合 revision 审批快照。"""
        if (
            scope_field != "evidence"
            or requested_field != "evidence"
            or evidence_id is not None
        ):
            return self._immediate("invalid_scope")
        item = await self._experiences.get(experience_id)
        if item is None or item.status == "archived":
            return self._immediate("invalidated")
        snapshot = await self._fields.snapshot(experience_id, "evidence_new", None)
        if snapshot.revision != expected_revision:
            return self._immediate("invalidated")
        try:
            proposed = EvidenceCreate.model_validate(suggested_content).model_dump(
                mode="json"
            )
        except ValidationError:
            return self._immediate("invalid_value")
        return self._proposal(
            experience_id,
            "evidence",
            None,
            snapshot.value,
            proposed,
            snapshot.revision,
            snapshot.normalized_value,
        )

    async def apply_field(
        self,
        experience_id: int,
        field: str,
        proposed_value: Any,
        *,
        expected_revision: int,
        expected_value: Any,
    ) -> dict[str, Any]:
        """校验 guard 后只覆盖一个 ExperienceItem 字段。"""
        if field not in EXPERIENCE_TARGET_KEYS:
            return self._immediate("invalid_scope")
        item = await self._experience(experience_id)
        snapshot = await self._fields.snapshot(experience_id, field, None)
        if item.status == "archived" or (
            snapshot.revision != expected_revision
            or snapshot.normalized_value != expected_value
        ):
            return await self._invalidated(item, snapshot)
        try:
            request = ExperienceUpdate.model_validate({field: proposed_value})
        except ValidationError as error:
            raise ValueError("invalid proposed experience value") from error
        value = request.model_dump(exclude_unset=True)[field]
        ExperienceService._validate_merged_dates(item, {field: value})
        if normalize_field_value(field, value) == snapshot.normalized_value:
            return self._immediate("no_change")
        await self._fields.claim_experience_units(
            experience_id, {field: expected_revision}, {field}
        )
        updated = await self._experiences.update_fields(experience_id, {field: value})
        await self._fields.advance_experience_fields(updated, {field})
        updated = await self._refresh_completeness(updated)
        detail = await ExperienceService(self._session)._detail(updated)
        await enqueue_experience_index_sync(self._session, experience_id)
        current = await self._fields.snapshot(experience_id, field, None)
        return self._applied(current, detail)

    async def apply_evidence(
        self,
        experience_id: int,
        evidence_id: int,
        value: dict[str, Any],
        *,
        expected_revision: int,
        expected_value: Any,
    ) -> dict[str, Any]:
        """按 ID 整体覆盖一个 EvidenceItem，其他 Item 保持原值。"""
        item = await self._experience(experience_id)
        evidence = await self._evidence.get_for_experience(experience_id, evidence_id)
        if evidence is None:
            return await self._missing_evidence(item, evidence_id)
        snapshot = await self._fields.snapshot(experience_id, "action", evidence_id)
        current_value = self._evidence_value(evidence)
        if item.status == "archived" or (
            snapshot.revision != expected_revision or current_value != expected_value
        ):
            return await self._invalidated_evidence(
                item, evidence_id, current_value, snapshot.revision, snapshot.status
            )
        try:
            proposed = EvidenceCreate.model_validate(value).model_dump(mode="json")
        except ValidationError as error:
            raise ValueError("invalid proposed evidence value") from error
        if proposed == current_value:
            return self._immediate("no_change")
        await self._fields.claim_unit(
            experience_id,
            "evidence",
            expected_revision,
            ref_id=evidence_id,
        )
        evidence = await self._evidence.update_fields(evidence_id, proposed)
        await self._fields.advance_evidence_fields(experience_id, evidence)
        updated = await self._refresh_completeness(item)
        detail = await ExperienceService(self._session)._detail(updated)
        await enqueue_experience_index_sync(self._session, experience_id)
        current = await self._fields.snapshot(experience_id, "action", evidence_id)
        return {
            "outcome": "applied",
            "scope": {"field": "evidence", "evidence_id": evidence_id},
            "value": self._evidence_value(evidence),
            "revision": current.revision,
            "field_status": current.status,
            "experience": detail.model_dump(mode="json"),
        }

    async def append_evidence(
        self,
        experience_id: int,
        item_payload: dict[str, Any],
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        """集合 revision 未变化时创建 Evidence 并追加到末尾。"""
        item = await self._experience(experience_id)
        collection = await self._fields.snapshot(experience_id, "evidence_new", None)
        if item.status == "archived" or collection.revision != expected_revision:
            return await self._invalidated(item, collection)
        await self._fields.claim_collection(experience_id, expected_revision)
        evidence = await self._evidence.create(EvidenceItem(**item_payload))
        updated = await self._experiences.set_evidence_ids(
            experience_id,
            [*ordered_evidence_ids(item), evidence.id],
        )
        await self._fields.initialize_evidence(experience_id, evidence)
        await self._fields.advance_collection(updated)
        updated = await self._refresh_completeness(updated)
        detail = await ExperienceService(self._session)._detail(updated)
        await enqueue_experience_index_sync(self._session, experience_id)
        collection = await self._fields.snapshot(experience_id, "evidence_new", None)
        return {
            "outcome": "applied",
            "scope": {"field": "evidence", "evidence_id": evidence.id},
            "created": True,
            "evidence_id": evidence.id,
            "evidence_ids": ordered_evidence_ids(updated),
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
        return {
            "outcome": "invalidated",
            "scope": {"field": "evidence", "evidence_id": evidence_id},
            "current_value": None,
            "experience": detail.model_dump(mode="json"),
        }

    async def _invalidated_evidence(
        self,
        item: ExperienceItem,
        evidence_id: int,
        current_value: dict[str, Any],
        revision: int,
        status: str,
    ) -> dict[str, Any]:
        """完整 EvidenceItem guard 失效时返回该 Item 当前快照。"""
        detail = await ExperienceService(self._session)._detail(item)
        return {
            "outcome": "invalidated",
            "scope": {"field": "evidence", "evidence_id": evidence_id},
            "current_value": current_value,
            "revision": revision,
            "field_status": status,
            "experience": detail.model_dump(mode="json"),
        }

    async def _refresh_completeness(self, item: ExperienceItem) -> ExperienceItem:
        evidence = await self._evidence.list_for_experience(item.experience_id)
        guidance = calculate_completeness(
            item, evidence, language=get_content_language()
        )
        updated = await self._experiences.set_completeness(
            item.experience_id, guidance.completeness
        )
        if (
            updated.status == "ready"
            and guidance.completeness < READY_COMPLETENESS_THRESHOLD
        ):
            updated = await self._experiences.set_status(item.experience_id, "draft")
        return updated

    async def _invalidated(
        self,
        item: ExperienceItem,
        snapshot: Any,
    ) -> dict[str, Any]:
        detail = await ExperienceService(self._session)._detail(item)
        return {
            "outcome": "invalidated",
            "scope": {"field": snapshot.key, "evidence_id": snapshot.ref_id},
            "current_value": snapshot.value,
            "revision": snapshot.revision,
            "experience": detail.model_dump(mode="json"),
        }

    @staticmethod
    def _applied(snapshot: Any, detail: ExperienceDetail) -> dict[str, Any]:
        return {
            "outcome": "applied",
            "scope": {"field": snapshot.key, "evidence_id": snapshot.ref_id},
            "value": snapshot.value,
            "revision": snapshot.revision,
            "field_status": snapshot.status,
            "experience": detail.model_dump(mode="json"),
        }

    @staticmethod
    def _immediate(outcome: str) -> dict[str, Any]:
        """返回无需审批的稳定业务结果。"""
        return {"outcome": outcome}

    @staticmethod
    def _evidence_value(item: EvidenceItem) -> dict[str, Any]:
        """返回一个 EvidenceItem 可用于比较和覆盖的完整内容。"""
        return {
            "background": item.background,
            "action": item.action,
            "result": item.result,
        }

    @staticmethod
    def _proposal(
        experience_id: int,
        field: str,
        evidence_id: int | None,
        current_content: Any,
        suggested_content: Any,
        revision: int,
        normalized_current_content: Any,
    ) -> PreparedExperienceChange:
        """构造统一 content_change 提案和审批 guard。"""
        scope = {"field": field, "evidence_id": evidence_id}
        return PreparedExperienceChange(
            proposal_payload={
                "scope": scope,
                "current_content": current_content,
                "suggested_content": suggested_content,
            },
            guard_payload={
                "experience_id": experience_id,
                "scope": scope,
                "revision": revision,
                "normalized_current_content": normalized_current_content,
            },
        )
