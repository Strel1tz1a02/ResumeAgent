"""Application service for person-level experience library records."""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config_cache import get_content_language
from app.models import ExperienceItem
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.experience_repository import ExperienceRepository, ExperienceStaleWriteError
from app.schemas.evidence_items import EvidenceRead
from app.schemas.experiences import (
    DeletionImpactResponse,
    ExperienceCreate,
    ExperienceDetail,
    ExperienceListQuery,
    ExperienceListResponse,
    ExperienceRead,
    ExperienceUpdate,
)
from app.services.experience_completeness_service import (
    READY_COMPLETENESS_THRESHOLD,
    calculate_completeness,
)
from app.services.experience_field_service import (
    ExperienceFieldService,
    FieldRevisionConflictError,
)

_NON_NULLABLE_UPDATE_FIELDS = frozenset(
    {"kind", "title", "is_current", "technologies", "tags"}
)


class ExperienceDomainError(Exception):
    """Base class for expected experience-library application errors."""


class ExperienceNotFoundError(ExperienceDomainError):
    """Raised when an experience identifier does not resolve to a record."""


class ExperienceConflictError(ExperienceDomainError):
    """Raised when an otherwise valid mutation conflicts with stored state."""


class ExperienceReadyConflictError(ExperienceConflictError):
    """Raised when a draft does not yet meet the readiness threshold."""

    def __init__(self, completeness: int, missing_dimensions: list[str]) -> None:
        super().__init__("Experience is not complete enough to mark ready")
        self.completeness = completeness
        self.missing_dimensions = missing_dimensions


class ExperienceValidationError(ExperienceDomainError):
    """Raised for business-rule validation errors after request parsing."""


class ExperienceService:
    """Own experience transactions, derived completeness, and response assembly."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._experiences = ExperienceRepository(session)
        self._evidence = EvidenceRepository(session)
        self._fields = ExperienceFieldService(session)

    async def create(self, request: ExperienceCreate) -> ExperienceDetail:
        """Create a draft record and persist its authoritative completeness score."""
        fields = request.model_dump()
        fields["kind"] = request.kind.value
        try:
            item = await self._experiences.create(
                ExperienceItem(
                    **fields,
                    evidence_ids=[],
                    status="draft",
                    completeness=0,
                )
            )
            await self._fields.initialize_experience(item)
            await self._recalculate_completeness(item)
            detail = await self._detail(item)
            await self._session.commit()
            return detail
        except Exception:
            await self._session.rollback()
            raise

    async def get(self, experience_id: int) -> ExperienceDetail:
        """Return one record with evidence expanded in its stored order."""
        item = await self._get_or_raise(experience_id)
        return await self._detail(item)

    async def list(self, query: ExperienceListQuery) -> ExperienceListResponse:
        """List concise experience rows under the repository's search/filter contract."""
        try:
            rows = await self._experiences.list(
                q=query.q,
                kind=query.kind.value if query.kind is not None else None,
                status=query.status,
                sort=query.sort,
            )
        except ValueError as error:
            raise ExperienceValidationError(str(error)) from error
        return ExperienceListResponse(
            items=[self._read(row) for row in rows],
            total=len(rows),
        )

    async def patch(self, experience_id: int, request: ExperienceUpdate) -> ExperienceDetail:
        """Update editable fields, validating merged state and refreshing completeness."""
        fields = request.model_dump(exclude_unset=True)
        expected_field_revisions = fields.pop("expected_field_revisions", {})
        if "kind" in fields and fields["kind"] is not None:
            fields["kind"] = request.kind.value

        try:
            await self._experiences.acquire_ownership_write_lock()
            existing = await self._get_or_raise(experience_id)
            existing = await self._repair_evidence_references(existing)
            observed_updated_at = existing.updated_at
            self._reject_null_non_nullable_fields(fields)
            self._validate_merged_dates(existing, fields)
            changed_keys = {
                key for key, value in fields.items() if getattr(existing, key) != value
            }
            await self._fields.validate_expected(
                experience_id, expected_field_revisions, changed_keys
            )
            updated = await self._experiences.update_fields_if_current(
                experience_id,
                observed_updated_at,
                fields,
            )
            if changed_keys:
                await self._fields.advance_experience_fields(updated, changed_keys)
            await self._recalculate_completeness(updated)
            detail = await self._detail(updated)
            await self._session.commit()
            return detail
        except (ExperienceStaleWriteError, FieldRevisionConflictError) as error:
            await self._session.rollback()
            raise ExperienceConflictError(
                f"Experience {experience_id} was updated by another request; reload and try again"
            ) from error
        except ExperienceDomainError:
            await self._session.rollback()
            raise
        except ValueError as error:
            await self._session.rollback()
            raise ExperienceValidationError(str(error)) from error
        except Exception:
            await self._session.rollback()
            raise

    async def mark_ready(self, experience_id: int) -> ExperienceDetail:
        """Promote a sufficiently complete active record to ready in one write transaction."""
        try:
            await self._experiences.acquire_ownership_write_lock()
            item = await self._get_or_raise(experience_id)
            item = await self._repair_evidence_references(item)
            if item.status == "archived":
                raise ExperienceConflictError(
                    f"Experience {experience_id} is archived; restore it before marking ready"
                )
            guidance = await self._guidance(item)
            if guidance.completeness < READY_COMPLETENESS_THRESHOLD:
                raise ExperienceReadyConflictError(
                    guidance.completeness, guidance.missing_dimensions
                )
            updated = await self._experiences.set_status(experience_id, "ready")
            detail = await self._detail(updated)
            await self._session.commit()
            return detail
        except ExperienceDomainError:
            await self._session.rollback()
            raise
        except ValueError as error:
            await self._session.rollback()
            raise ExperienceValidationError(str(error)) from error
        except Exception:
            await self._session.rollback()
            raise

    async def archive(self, experience_id: int) -> ExperienceDetail:
        """Archive a record as the reversible normal-delete lifecycle action."""
        return await self._transition_status(experience_id, "archived")

    async def restore(self, experience_id: int) -> ExperienceDetail:
        """Restore an archived record as a draft regardless of its former readiness."""
        try:
            await self._experiences.acquire_ownership_write_lock()
            item = await self._get_or_raise(experience_id)
            if item.status != "archived":
                raise ExperienceConflictError(
                    f"Experience {experience_id} must be archived before it can be restored"
                )
            item = await self._repair_evidence_references(item)
            updated = await self._experiences.set_status(item.experience_id, "draft")
            detail = await self._detail(updated)
            await self._session.commit()
            return detail
        except ExperienceDomainError:
            await self._session.rollback()
            raise
        except ValueError as error:
            await self._session.rollback()
            raise ExperienceValidationError(str(error)) from error
        except Exception:
            await self._session.rollback()
            raise

    async def deletion_impact(self, experience_id: int) -> DeletionImpactResponse:
        """Return the stable deletion-review shape without consulting future match/resume links."""
        await self._get_or_raise(experience_id)
        return DeletionImpactResponse(affected_matches=[], affected_resumes=[])

    async def permanently_delete(self, experience_id: int) -> None:
        """Irreversibly delete an archived record and only its currently owned evidence."""
        try:
            await self._experiences.acquire_ownership_write_lock()
            item = await self._get_or_raise(experience_id)
            if item.status != "archived":
                raise ExperienceConflictError(
                    f"Experience {experience_id} must be archived before permanent deletion"
                )
            owned_evidence_ids = list(item.evidence_ids or [])
            deleted = await self._experiences.delete(experience_id)
            if not deleted:
                raise ExperienceNotFoundError(f"Experience {experience_id} was not found")
            for evidence_id in owned_evidence_ids:
                await self._evidence.delete(evidence_id)
            await self._session.commit()
        except ExperienceDomainError:
            await self._session.rollback()
            raise
        except ValueError as error:
            await self._session.rollback()
            raise ExperienceValidationError(str(error)) from error
        except Exception:
            await self._session.rollback()
            raise

    async def _transition_status(
        self,
        experience_id: int,
        target_status: Literal["draft", "ready", "archived"],
    ) -> ExperienceDetail:
        """Serialize lifecycle writes so a stale action cannot silently overwrite another action."""
        try:
            await self._experiences.acquire_ownership_write_lock()
            item = await self._get_or_raise(experience_id)
            await self._repair_evidence_references(item)
            updated = await self._experiences.set_status(experience_id, target_status)
            detail = await self._detail(updated)
            await self._session.commit()
            return detail
        except ExperienceDomainError:
            await self._session.rollback()
            raise
        except ValueError as error:
            await self._session.rollback()
            raise ExperienceValidationError(str(error)) from error
        except Exception:
            await self._session.rollback()
            raise

    async def _get_or_raise(self, experience_id: int) -> ExperienceItem:
        item = await self._experiences.get(experience_id)
        if item is None:
            raise ExperienceNotFoundError(f"Experience {experience_id} was not found")
        return item

    async def _repair_evidence_references(self, item: ExperienceItem) -> ExperienceItem:
        """Normalize tolerated historical JSON corruption before any write propagates it."""
        evidence_items = await self._evidence.get_many_ordered(item.evidence_ids or [])
        valid_ids = list(dict.fromkeys(evidence.id for evidence in evidence_items))
        return await self._experiences.set_evidence_ids_if_current(
            item.experience_id,
            item.updated_at,
            valid_ids,
        )

    async def _recalculate_completeness(self, item: ExperienceItem) -> None:
        evidence_items = await self._evidence.get_many_ordered(item.evidence_ids or [])
        result = calculate_completeness(
            item, evidence_items, language=get_content_language()
        )
        updated = await self._experiences.set_completeness(item.experience_id, result.completeness)
        if updated.status == "ready" and result.completeness < READY_COMPLETENESS_THRESHOLD:
            await self._experiences.set_status(item.experience_id, "draft")

    async def _detail(self, item: ExperienceItem) -> ExperienceDetail:
        evidence_items, guidance = await self._evidence_and_guidance(item)
        field_states = await self._fields.list_states(item.experience_id)
        return ExperienceDetail(
            **self._read(item).model_dump(),
            evidence_items=[self._evidence_read(evidence) for evidence in evidence_items],
            missing_dimensions=guidance.missing_dimensions,
            suggested_questions=guidance.suggested_questions,
            field_states=[
                {
                    "key": state.target_key,
                    "ref_id": state.ref_id or None,
                    "status": state.status,
                    "revision": state.revision,
                }
                for state in field_states
            ],
        )

    async def _guidance(self, item: ExperienceItem):
        """Calculate live completeness without trusting a potentially stale persisted score."""
        _, guidance = await self._evidence_and_guidance(item)
        return guidance

    async def _evidence_and_guidance(self, item: ExperienceItem):
        evidence_items = await self._evidence.get_many_ordered(item.evidence_ids or [])
        return evidence_items, calculate_completeness(
            item, evidence_items, language=get_content_language()
        )

    @staticmethod
    def _validate_merged_dates(item: ExperienceItem, fields: dict[str, Any]) -> None:
        is_current = fields.get("is_current", item.is_current)
        end_date = fields.get("end_date", item.end_date)
        if is_current and end_date is not None:
            raise ExperienceValidationError("current experiences cannot have an end_date")

    @staticmethod
    def _reject_null_non_nullable_fields(fields: dict[str, Any]) -> None:
        null_fields = sorted(
            name for name in _NON_NULLABLE_UPDATE_FIELDS if name in fields and fields[name] is None
        )
        if null_fields:
            raise ExperienceValidationError(
                f"non-nullable experience fields cannot be null: {', '.join(null_fields)}"
            )

    @staticmethod
    def _read(item: ExperienceItem) -> ExperienceRead:
        return ExperienceRead(
            experience_id=item.experience_id,
            kind=item.kind,
            title=item.title,
            organization=item.organization,
            role=item.role,
            location=item.location,
            start_date=item.start_date,
            end_date=item.end_date,
            is_current=item.is_current,
            background=item.background,
            evidence_ids=item.evidence_ids or [],
            technologies=item.technologies or [],
            tags=item.tags or [],
            notes=item.notes,
            status=item.status,
            completeness=item.completeness,
            archived_at=item.archived_at,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    @staticmethod
    def _evidence_read(item: Any) -> EvidenceRead:
        return EvidenceRead(
            id=item.id,
            action=item.action,
            result=item.result,
            metrics=item.metrics,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
