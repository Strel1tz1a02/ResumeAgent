"""结构化经历证据的事务性修改。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.config_cache import get_content_language
from app.models import EvidenceItem, ExperienceItem
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.experience_repository import ExperienceRepository, ExperienceStaleWriteError
from app.schemas.evidence_items import EvidenceCreate, EvidenceReorder, EvidenceUpdate
from app.schemas.experiences import ExperienceDetail
from app.services.experience_completeness_service import (
    READY_COMPLETENESS_THRESHOLD,
    calculate_completeness,
)
from app.services.experience_field_service import (
    ExperienceFieldService,
    FieldRevisionConflictError,
)
from app.services.experience_service import (
    ExperienceConflictError,
    ExperienceNotFoundError,
    ExperienceService,
    ExperienceValidationError,
)


class EvidenceService:
    """在一次提交中维护证据记录、JSON 引用和派生经历状态。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._experiences = ExperienceRepository(session)
        self._evidence = EvidenceRepository(session)
        self._fields = ExperienceFieldService(session)

    async def create(self, experience_id: int, request: EvidenceCreate) -> ExperienceDetail:
        """插入证据、追加其引用，并返回原子刷新的详情。"""
        async def mutation(item: ExperienceItem, observed_updated_at: str) -> ExperienceItem:
            evidence = await self._evidence.create(EvidenceItem(**request.model_dump()))
            updated = await self._experiences.set_evidence_ids_if_current(
                item.experience_id,
                observed_updated_at,
                [*(item.evidence_ids or []), evidence.id],
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
        expected_revision = fields.pop("expected_revision", None)
        if fields.get("action", object()) is None:
            raise ExperienceValidationError("evidence action cannot be null")

        async def mutation(item: ExperienceItem, _observed_updated_at: str) -> ExperienceItem:
            evidence = await self._get_owned_evidence_or_raise(item, evidence_id)
            if expected_revision is not None:
                state = await self._fields.require_state(
                    item.experience_id, "action", evidence_id
                )
                if state.revision != expected_revision:
                    raise FieldRevisionConflictError("stale evidence revision")
            changed = any(getattr(evidence, key) != value for key, value in fields.items())
            if changed:
                evidence = await self._evidence.update_fields(evidence_id, fields)
                await self._fields.advance_evidence_fields(item.experience_id, evidence)
            return item

        return await self._mutate(experience_id, mutation)

    async def delete(self, experience_id: int, evidence_id: int) -> ExperienceDetail:
        """在同一事务中解除关联并删除所属证据记录。"""
        async def mutation(item: ExperienceItem, observed_updated_at: str) -> ExperienceItem:
            await self._get_owned_evidence_or_raise(item, evidence_id)
            detached = await self._experiences.set_evidence_ids_if_current(
                item.experience_id,
                observed_updated_at,
                [item_id for item_id in (item.evidence_ids or []) if item_id != evidence_id],
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

        async def mutation(item: ExperienceItem, observed_updated_at: str) -> ExperienceItem:
            current_ids = item.evidence_ids or []
            if (
                len(requested_ids) != len(set(requested_ids))
                or len(requested_ids) != len(current_ids)
                or set(requested_ids) != set(current_ids)
            ):
                raise ExperienceValidationError(
                    "evidence_ids must contain exactly the current unique evidence IDs"
                )
            updated = await self._experiences.set_evidence_ids_if_current(
                item.experience_id, observed_updated_at, requested_ids
            )
            if requested_ids != current_ids:
                await self._fields.advance_collection(updated)
            return updated

        return await self._mutate(experience_id, mutation)

    async def _mutate(
        self,
        experience_id: int,
        mutation: Callable[[ExperienceItem, str], Awaitable[ExperienceItem]],
    ) -> ExperienceDetail:
        try:
            await self._experiences.acquire_ownership_write_lock()
            item = await self._get_experience_or_raise(experience_id)
        # 修改证据记录前先占用经历版本，使局部更新与 JSON 引用修改遵守同一
        # 乐观并发边界；随后 SQLite 会在证据和派生状态更新期间保持写事务。
            claimed = await self._experiences.set_evidence_ids_if_current(
                item.experience_id, item.updated_at, item.evidence_ids or []
            )
            updated = await mutation(claimed, claimed.updated_at)
            updated = await self._recalculate_completeness(updated)
            detail = await self._detail(updated)
            await self._session.commit()
            return detail
        except (ExperienceStaleWriteError, FieldRevisionConflictError) as error:
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
        if evidence_id not in (experience.evidence_ids or []):
            raise ExperienceNotFoundError(f"Evidence {evidence_id} was not found")
        evidence = await self._evidence.get(evidence_id)
        if evidence is None:
            raise ExperienceNotFoundError(f"Evidence {evidence_id} was not found")
        return evidence

    async def _recalculate_completeness(self, item: ExperienceItem) -> ExperienceItem:
        evidence_items = await self._evidence.get_many_ordered(item.evidence_ids or [])
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
