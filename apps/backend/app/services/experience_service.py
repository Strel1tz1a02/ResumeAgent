"""Application service for person-level experience library records."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ExperienceItem
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.experience_repository import ExperienceRepository
from app.schemas.evidence_items import EvidenceRead
from app.schemas.experiences import (
    ExperienceCreate,
    ExperienceDetail,
    ExperienceListQuery,
    ExperienceListResponse,
    ExperienceRead,
    ExperienceUpdate,
)
from app.services.experience_completeness_service import calculate_completeness

_NON_NULLABLE_UPDATE_FIELDS = frozenset(
    {"kind", "title", "is_current", "raw_input", "technologies", "tags"}
)


class ExperienceDomainError(Exception):
    """Base class for expected experience-library application errors."""


class ExperienceNotFoundError(ExperienceDomainError):
    """Raised when an experience identifier does not resolve to a record."""


class ExperienceConflictError(ExperienceDomainError):
    """Raised when an otherwise valid mutation conflicts with stored state."""


class ExperienceValidationError(ExperienceDomainError):
    """Raised for business-rule validation errors after request parsing."""


class ExperienceService:
    """Own experience transactions, derived completeness, and response assembly."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._experiences = ExperienceRepository(session)
        self._evidence = EvidenceRepository(session)

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
        if "kind" in fields and fields["kind"] is not None:
            fields["kind"] = request.kind.value

        try:
            existing = await self._get_or_raise(experience_id)
            self._reject_null_non_nullable_fields(fields)
            self._validate_merged_dates(existing, fields)
            updated = await self._experiences.update_fields(experience_id, fields)
            await self._recalculate_completeness(updated)
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

    async def _recalculate_completeness(self, item: ExperienceItem) -> None:
        evidence_items = await self._evidence.get_many_ordered(item.evidence_ids or [])
        result = calculate_completeness(item, evidence_items)
        await self._experiences.set_completeness(item.experience_id, result.completeness)

    async def _detail(self, item: ExperienceItem) -> ExperienceDetail:
        evidence_items = await self._evidence.get_many_ordered(item.evidence_ids or [])
        guidance = calculate_completeness(item, evidence_items)
        return ExperienceDetail(
            **self._read(item).model_dump(),
            evidence_items=[self._evidence_read(evidence) for evidence in evidence_items],
            missing_dimensions=guidance.missing_dimensions,
            suggested_questions=guidance.suggested_questions,
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
            raw_input=item.raw_input,
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
