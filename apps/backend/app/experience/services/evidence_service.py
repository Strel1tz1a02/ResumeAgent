"""结构化经历证据的事务性修改。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.config_cache import get_content_language
from app.models import EvidenceItem, ExperienceItem
from app.experience.repositories.evidence_repository import EvidenceRepository
from app.experience.repositories.experience_repository import (
    ExperienceRepository,
    ordered_evidence_ids,
)
from app.experience.schemas.evidence_items import EvidenceCreateRequest, EvidenceReorder, EvidenceUpdate
from app.experience.schemas.experiences import ExperienceDetail
from app.experience.services.experience_completeness_service import (
    READY_COMPLETENESS_THRESHOLD,
    calculate_completeness,
)
from app.experience.services.experience_field_service import (
    ExperienceFieldService,
    FieldRevisionConflictError,
)
from app.experience.services.experience_service import (
    ExperienceConflictError,
    ExperienceNotFoundError,
    ExperienceService,
    ExperienceValidationError,
)


class EvidenceService:
    """在一次提交中维护证据、关系顺序和派生经历状态。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._experiences = ExperienceRepository(session)
        self._evidence = EvidenceRepository(session)
        self._fields = ExperienceFieldService(session)

    async def create(
        self, experience_id: int, request: EvidenceCreateRequest
    ) -> ExperienceDetail:
        """插入证据、追加其引用，并返回原子刷新的详情。"""
        async def mutation(item: ExperienceItem) -> ExperienceItem:
            await self._fields.claim_collection(
                experience_id, request.expected_collection_revision
            )
            evidence_payload = request.model_dump(exclude={"expected_collection_revision"})
            evidence = await self._evidence.create(EvidenceItem(**evidence_payload))
            updated = await self._experiences.set_evidence_ids(
                item.experience_id,
                [*ordered_evidence_ids(item), evidence.id],
            )
            await self._fields.initialize_evidence(item.experience_id, evidence)
            await self._fields.advance_collection(updated)
            return updated

        return await self._mutate(experience_id, mutation)

    async def patch(
        self, experience_id: int, evidence_id: int, request: EvidenceUpdate
    ) -> ExperienceDetail:
        """确认归属当前经历后，才更新对应证据记录。"""
        fields = request.model_dump(exclude_unset=True)
        expected_revision = fields.pop("expected_revision")
        if fields.get("action", object()) is None:
            raise ExperienceValidationError("evidence action cannot be null")

        async def mutation(item: ExperienceItem) -> ExperienceItem:
            evidence = await self._get_owned_evidence_or_raise(item, evidence_id)
            changed = any(getattr(evidence, key) != value for key, value in fields.items())
            if changed:
                await self._fields.claim_unit(
                    item.experience_id,
                    "evidence",
                    expected_revision,
                    ref_id=evidence_id,
                )
                evidence = await self._evidence.update_fields(evidence_id, fields)
                await self._fields.advance_evidence_fields(item.experience_id, evidence)
            return item

        return await self._mutate(experience_id, mutation)

    async def delete(
        self,
        experience_id: int,
        evidence_id: int,
        *,
        expected_revision: int,
        expected_collection_revision: int,
    ) -> ExperienceDetail:
        """在同一事务中解除关联并删除所属证据记录。"""
        async def mutation(item: ExperienceItem) -> ExperienceItem:
            await self._get_owned_evidence_or_raise(item, evidence_id)
            await self._fields.claim_collection(
                item.experience_id, expected_collection_revision
            )
            await self._fields.claim_unit(
                item.experience_id,
                "evidence",
                expected_revision,
                ref_id=evidence_id,
            )
            detached = await self._experiences.set_evidence_ids(
                item.experience_id,
                [
                    item_id
                    for item_id in ordered_evidence_ids(item)
                    if item_id != evidence_id
                ],
            )
            deleted = await self._evidence.delete(evidence_id)
            if not deleted:
                raise ExperienceNotFoundError(f"Evidence {evidence_id} was not found")
            await self._fields.delete_evidence_states(item.experience_id, evidence_id)
            await self._fields.advance_collection(detached)
            return detached

        return await self._mutate(experience_id, mutation)

    async def reorder(self, experience_id: int, request: EvidenceReorder) -> ExperienceDetail:
        """仅当客户端提供完整的当前 ID 集合时替换展示顺序。"""
        requested_ids = request.evidence_ids

        async def mutation(item: ExperienceItem) -> ExperienceItem:
            current_ids = ordered_evidence_ids(item)
            if (
                len(requested_ids) != len(set(requested_ids))
                or len(requested_ids) != len(current_ids)
                or set(requested_ids) != set(current_ids)
            ):
                raise ExperienceValidationError(
                    "evidence_ids must contain exactly the current unique evidence IDs"
                )
            if requested_ids != current_ids:
                await self._fields.claim_collection(
                    item.experience_id, request.expected_collection_revision
                )
                updated = await self._experiences.set_evidence_ids(
                    item.experience_id, requested_ids
                )
                await self._fields.advance_collection(updated)
                return updated
            return item

        return await self._mutate(experience_id, mutation)

    async def _mutate(
        self,
        experience_id: int,
        mutation: Callable[[ExperienceItem], Awaitable[ExperienceItem]],
    ) -> ExperienceDetail:
        try:
            item = await self._get_experience_or_raise(experience_id)
            updated = await mutation(item)
            updated = await self._recalculate_completeness(updated)
            detail = await self._detail(updated)
            await self._session.commit()
            return detail
        except FieldRevisionConflictError as error:
            await self._session.rollback()
            raise ExperienceConflictError(
                f"Experience {experience_id} was updated by another request; reload and try again"
            ) from error
        except (ExperienceNotFoundError, ExperienceValidationError, ExperienceConflictError):
            await self._session.rollback()
            raise
        except ValueError as error:
            await self._session.rollback()
            raise ExperienceValidationError(str(error)) from error
        except Exception:
            await self._session.rollback()
            raise

    async def _get_experience_or_raise(self, experience_id: int) -> ExperienceItem:
        item = await self._experiences.get(experience_id)
        if item is None:
            raise ExperienceNotFoundError(f"Experience {experience_id} was not found")
        return item

    async def _get_owned_evidence_or_raise(
        self, experience: ExperienceItem, evidence_id: int
    ) -> EvidenceItem:
        evidence = await self._evidence.get_for_experience(
            experience.experience_id, evidence_id
        )
        if evidence is None:
            raise ExperienceNotFoundError(f"Evidence {evidence_id} was not found")
        return evidence

    async def _recalculate_completeness(self, item: ExperienceItem) -> ExperienceItem:
        evidence_items = await self._evidence.list_for_experience(item.experience_id)
        result = calculate_completeness(
            item, evidence_items, language=get_content_language()
        )
        updated = await self._experiences.set_completeness(item.experience_id, result.completeness)
        if updated.status == "ready" and result.completeness < READY_COMPLETENESS_THRESHOLD:
            updated = await self._experiences.set_status(item.experience_id, "draft")
        return updated

    async def _detail(self, item: ExperienceItem) -> ExperienceDetail:
        """复用经历详情组装，确保字段状态和 revision 始终返回。"""
        return await ExperienceService(self._session)._detail(item)
