"""整个经历表单的原子保存服务。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EvidenceItem
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.experience_repository import ExperienceRepository, ExperienceStaleWriteError
from app.schemas.experiences import ExperienceDetail, ExperienceGlobalSave
from app.services.experience_field_service import ExperienceFieldService, FieldRevisionConflictError
from app.services.experience_service import (
    ExperienceConflictError,
    ExperienceNotFoundError,
    ExperienceService,
    ExperienceValidationError,
)


class ExperienceGlobalSaveService:
    """先校验所有保存单元，再以单一事务写入整个经历。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._experiences = ExperienceRepository(session)
        self._evidence = EvidenceRepository(session)
        self._fields = ExperienceFieldService(session)
        self._experience_service = ExperienceService(session)

    async def save(
        self, experience_id: int, request: ExperienceGlobalSave
    ) -> ExperienceDetail:
        """原子保存全部主字段、现有 Evidence 和可选的新 Evidence。"""
        try:
            await self._experiences.acquire_ownership_write_lock()
            item = await self._experiences.get(experience_id)
            if item is None:
                raise ExperienceNotFoundError(f"Experience {experience_id} was not found")
            if item.status == "archived":
                raise ExperienceConflictError("Archived experiences cannot be edited")

            experience_fields = request.experience.model_dump(exclude_unset=True)
            expected_revisions = experience_fields.pop("expected_field_revisions", {})
            if "kind" in experience_fields and experience_fields["kind"] is not None:
                experience_fields["kind"] = request.experience.kind.value
            ExperienceService._reject_null_non_nullable_fields(experience_fields)
            ExperienceService._validate_merged_dates(item, experience_fields)

            evidence_ids = list(item.evidence_ids or [])
            requested_ids = [value.evidence_id for value in request.evidence_items]
            if requested_ids != evidence_ids:
                raise ExperienceConflictError("Evidence collection changed; reload and try again")
            changed_experience = {
                key
                for key, value in experience_fields.items()
                if getattr(item, key) != value
            }
            await self._fields.validate_expected(
                experience_id, expected_revisions, changed_experience
            )

            evidence_changes: list[tuple[EvidenceItem, dict[str, object]]] = []
            for value in request.evidence_items:
                evidence = await self._evidence.get(value.evidence_id)
                if evidence is None:
                    raise ExperienceConflictError("Evidence collection changed; reload and try again")
                state = await self._fields.require_state(
                    experience_id, "action", value.evidence_id
                )
                if state.revision != value.expected_revision:
                    raise FieldRevisionConflictError("stale evidence revision")
                fields: dict[str, object] = {
                    "action": value.action.strip(),
                    "result": value.result,
                    "metrics": value.metrics,
                }
                changed = {
                    key: field_value
                    for key, field_value in fields.items()
                    if getattr(evidence, key) != field_value
                }
                evidence_changes.append((evidence, changed))

            collection_state = await self._fields.require_state(
                experience_id, "evidence_new"
            )
            if collection_state.revision != request.expected_collection_revision:
                raise FieldRevisionConflictError("stale evidence collection revision")

            if changed_experience:
                item = await self._experiences.update_fields_if_current(
                    experience_id, item.updated_at, experience_fields
                )
                await self._fields.advance_experience_fields(item, changed_experience)
            for evidence, changed in evidence_changes:
                if not changed:
                    continue
                evidence = await self._evidence.update_fields(evidence.id, changed)
                await self._fields.advance_evidence_fields(experience_id, evidence)

            if request.new_evidence is not None:
                evidence = await self._evidence.create(
                    EvidenceItem(**request.new_evidence.model_dump())
                )
                item = await self._experiences.set_evidence_ids_if_current(
                    experience_id,
                    item.updated_at,
                    [*evidence_ids, evidence.id],
                )
                await self._fields.initialize_evidence(experience_id, evidence)
                await self._fields.advance_collection(item)

            await self._experience_service._recalculate_completeness(item)
            refreshed = await self._experiences.get(experience_id)
            if refreshed is None:
                raise ExperienceNotFoundError(f"Experience {experience_id} was not found")
            detail = await self._experience_service._detail(refreshed)
            await self._session.commit()
            return detail
        except (ExperienceStaleWriteError, FieldRevisionConflictError) as error:
            await self._session.rollback()
            raise ExperienceConflictError(
                f"Experience {experience_id} was updated by another request; reload and try again"
            ) from error
        except (ExperienceNotFoundError, ExperienceConflictError, ExperienceValidationError):
            await self._session.rollback()
            raise
        except ValueError as error:
            await self._session.rollback()
            raise ExperienceValidationError(str(error)) from error
        except Exception:
            await self._session.rollback()
            raise
