"""整个经历聚合的原子 upsert 服务。"""

from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.experience.models import EvidenceItem, ExperienceItem
from app.experience.repositories.evidence_repository import EvidenceRepository
from app.experience.repositories.experience_repository import (
    ExperienceRepository,
    ordered_evidence_ids,
)
from app.experience.schemas.experiences import (
    ExperienceCreate,
    ExperienceDetail,
    ExperienceEvidenceSave,
    ExperienceGlobalSave,
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


class ExperienceGlobalSaveService:
    """有 ID 覆盖、无 ID 创建，并在同一事务中保存全部 Evidence。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._experiences = ExperienceRepository(session)
        self._evidence = EvidenceRepository(session)
        self._fields = ExperienceFieldService(session)
        self._experience_service = ExperienceService(session)

    async def save(self, request: ExperienceGlobalSave) -> ExperienceDetail:
        """根据 experience_id 选择创建或覆盖整个经历聚合。"""
        try:
            if request.experience_id is None:
                detail = await self._create(request)
            else:
                detail = await self._update(request)
            await self._session.commit()
            return detail
        except FieldRevisionConflictError as error:
            await self._session.rollback()
            subject = request.experience_id or "new"
            raise ExperienceConflictError(
                f"Experience {subject} was updated by another request; reload and try again"
            ) from error
        except (
            ExperienceNotFoundError,
            ExperienceConflictError,
            ExperienceValidationError,
        ):
            await self._session.rollback()
            raise
        except (ValidationError, ValueError) as error:
            await self._session.rollback()
            raise ExperienceValidationError(str(error)) from error
        except Exception:
            await self._session.rollback()
            raise

    async def _create(self, request: ExperienceGlobalSave) -> ExperienceDetail:
        create = ExperienceCreate.model_validate(
            request.experience.model_dump(
                exclude={"expected_field_revisions"}, exclude_unset=True
            )
        )
        fields = create.model_dump()
        fields["kind"] = create.kind.value
        item = await self._experiences.create(
            ExperienceItem(**fields, status="draft", completeness=0)
        )
        created_evidence: list[EvidenceItem] = []
        for value in request.evidence_items:
            created_evidence.append(await self._create_evidence(value))
        if created_evidence:
            item = await self._experiences.set_evidence_ids(
                item.experience_id, [evidence.id for evidence in created_evidence]
            )
        await self._fields.initialize_experience(item)
        for evidence in created_evidence:
            await self._fields.initialize_evidence(item.experience_id, evidence)
        await self._experience_service._recalculate_completeness(item)
        refreshed = await self._require_experience(item.experience_id)
        return await self._experience_service._detail(refreshed)

    async def _update(self, request: ExperienceGlobalSave) -> ExperienceDetail:
        experience_id = request.experience_id
        if experience_id is None or request.expected_collection_revision is None:
            raise ExperienceValidationError("existing experience requires revisions")
        item = await self._require_experience(experience_id)
        if item.status == "archived":
            raise ExperienceConflictError("Archived experiences cannot be edited")

        experience_fields = request.experience.model_dump(exclude_unset=True)
        expected_revisions = experience_fields.pop("expected_field_revisions", {})
        if "kind" in experience_fields and experience_fields["kind"] is not None:
            experience_fields["kind"] = request.experience.kind.value
        ExperienceService._reject_null_non_nullable_fields(experience_fields)
        ExperienceService._validate_merged_dates(item, experience_fields)
        changed_experience = {
            key
            for key, value in experience_fields.items()
            if getattr(item, key) != value
        }
        await self._fields.claim_experience_units(
            experience_id, expected_revisions, changed_experience
        )

        existing_ids = ordered_evidence_ids(item)
        requested_existing_ids = [
            value.evidence_id
            for value in request.evidence_items
            if value.evidence_id is not None
        ]
        if requested_existing_ids != existing_ids:
            raise ExperienceConflictError(
                "Evidence collection changed; reload and try again"
            )

        evidence_changes: list[
            tuple[ExperienceEvidenceSave, EvidenceItem, dict[str, object]]
        ] = []
        for value in request.evidence_items:
            if value.evidence_id is None:
                continue
            evidence = await self._evidence.get_for_experience(
                experience_id, value.evidence_id
            )
            if evidence is None:
                raise ExperienceConflictError(
                    "Evidence collection changed; reload and try again"
                )
            fields = self._evidence_fields(value)
            changed = {
                key: field_value
                for key, field_value in fields.items()
                if getattr(evidence, key) != field_value
            }
            evidence_changes.append((value, evidence, changed))

        new_values = [
            value for value in request.evidence_items if value.evidence_id is None
        ]
        if new_values:
            await self._fields.claim_collection(
                experience_id, request.expected_collection_revision
            )
        else:
            await self._fields.verify_collection(
                experience_id, request.expected_collection_revision
            )

        if changed_experience:
            item = await self._experiences.update_fields(
                experience_id, experience_fields
            )
            await self._fields.advance_experience_fields(item, changed_experience)
        for value, evidence, changed in evidence_changes:
            if not changed:
                continue
            if value.expected_revision is None:
                raise ExperienceValidationError("existing evidence requires a revision")
            await self._fields.claim_unit(
                experience_id,
                "evidence",
                value.expected_revision,
                ref_id=evidence.id,
            )
            evidence = await self._evidence.update_fields(evidence.id, changed)
            await self._fields.advance_evidence_fields(experience_id, evidence)

        created_by_index: dict[int, EvidenceItem] = {}
        for index, value in enumerate(request.evidence_items):
            if value.evidence_id is None:
                created_by_index[index] = await self._create_evidence(value)
        if created_by_index:
            final_ids = [
                value.evidence_id
                if value.evidence_id is not None
                else created_by_index[index].id
                for index, value in enumerate(request.evidence_items)
            ]
            item = await self._experiences.set_evidence_ids(experience_id, final_ids)
            for evidence in created_by_index.values():
                await self._fields.initialize_evidence(experience_id, evidence)
            await self._fields.advance_collection(item)

        await self._experience_service._recalculate_completeness(item)
        refreshed = await self._require_experience(experience_id)
        return await self._experience_service._detail(refreshed)

    async def _create_evidence(self, value: ExperienceEvidenceSave) -> EvidenceItem:
        return await self._evidence.create(EvidenceItem(**self._evidence_fields(value)))

    @staticmethod
    def _evidence_fields(value: ExperienceEvidenceSave) -> dict[str, object]:
        return {
            "background": value.background,
            "action": value.action.strip(),
            "result": value.result,
        }

    async def _require_experience(self, experience_id: int) -> ExperienceItem:
        item = await self._experiences.get(experience_id)
        if item is None:
            raise ExperienceNotFoundError(f"Experience {experience_id} was not found")
        return item
