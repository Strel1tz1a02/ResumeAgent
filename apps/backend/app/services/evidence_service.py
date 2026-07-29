"""Transactional mutations for structured experience evidence."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EvidenceItem, ExperienceItem
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.experience_repository import ExperienceRepository, ExperienceStaleWriteError
from app.schemas.evidence_items import EvidenceCreate, EvidenceReorder, EvidenceUpdate
from app.schemas.experiences import ExperienceDetail
from app.services.experience_completeness_service import (
    READY_COMPLETENESS_THRESHOLD,
    calculate_completeness,
)
from app.services.experience_service import (
    ExperienceConflictError,
    ExperienceNotFoundError,
    ExperienceService,
    ExperienceValidationError,
)


class EvidenceService:
    """Keep evidence rows, JSON references, and derived experience state in one commit."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._experiences = ExperienceRepository(session)
        self._evidence = EvidenceRepository(session)

    async def create(self, experience_id: int, request: EvidenceCreate) -> ExperienceDetail:
        """Insert evidence, append its reference, and return the atomically refreshed detail."""
        async def mutation(item: ExperienceItem, observed_updated_at: str) -> ExperienceItem:
            evidence = await self._evidence.create(EvidenceItem(**request.model_dump()))
            return await self._experiences.set_evidence_ids_if_current(
                item.experience_id,
                observed_updated_at,
                [*(item.evidence_ids or []), evidence.id],
            )

        return await self._mutate(experience_id, mutation)

    async def patch(
        self, experience_id: int, evidence_id: int, request: EvidenceUpdate
    ) -> ExperienceDetail:
        """Update one evidence row only after proving it belongs to this experience."""
        fields = request.model_dump(exclude_unset=True)
        if fields.get("action", object()) is None:
            raise ExperienceValidationError("evidence action cannot be null")

        async def mutation(item: ExperienceItem, _observed_updated_at: str) -> ExperienceItem:
            await self._get_owned_evidence_or_raise(item, evidence_id)
            await self._evidence.update_fields(evidence_id, fields)
            return item

        return await self._mutate(experience_id, mutation)

    async def delete(self, experience_id: int, evidence_id: int) -> ExperienceDetail:
        """Detach and delete an owned evidence row in the same transaction."""
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
            return detached

        return await self._mutate(experience_id, mutation)

    async def reorder(self, experience_id: int, request: EvidenceReorder) -> ExperienceDetail:
        """Replace display order only when the client supplies the exact current ID set."""
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
            return await self._experiences.set_evidence_ids_if_current(
                item.experience_id, observed_updated_at, requested_ids
            )

        return await self._mutate(experience_id, mutation)

    async def _mutate(
        self,
        experience_id: int,
        mutation: Callable[[ExperienceItem, str], Awaitable[ExperienceItem]],
    ) -> ExperienceDetail:
        try:
            await self._experiences.acquire_ownership_write_lock()
            item = await self._get_experience_or_raise(experience_id)
            # Claim the experience version before touching an evidence row.  This
            # keeps patch operations subject to the same optimistic concurrency
            # boundary as JSON-reference changes; SQLite then holds the write
            # transaction through the evidence and derived-state updates.
            claimed = await self._experiences.set_evidence_ids_if_current(
                item.experience_id, item.updated_at, item.evidence_ids or []
            )
            updated = await mutation(claimed, claimed.updated_at)
            updated = await self._recalculate_completeness(updated)
            detail = await self._detail(updated)
            await self._session.commit()
            return detail
        except ExperienceStaleWriteError as error:
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
        result = calculate_completeness(item, evidence_items)
        updated = await self._experiences.set_completeness(item.experience_id, result.completeness)
        if updated.status == "ready" and result.completeness < READY_COMPLETENESS_THRESHOLD:
            updated = await self._experiences.set_status(item.experience_id, "draft")
        return updated

    async def _detail(self, item: ExperienceItem) -> ExperienceDetail:
        evidence_items = await self._evidence.get_many_ordered(item.evidence_ids or [])
        guidance = calculate_completeness(item, evidence_items)
        return ExperienceDetail(
            **ExperienceService._read(item).model_dump(),
            evidence_items=[ExperienceService._evidence_read(evidence) for evidence in evidence_items],
            missing_dimensions=guidance.missing_dimensions,
            suggested_questions=guidance.suggested_questions,
        )
