"""个人经历库记录的应用服务。"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config_cache import get_content_language
from app.models import ExperienceItem
from app.experience.repositories.evidence_repository import EvidenceRepository
from app.experience.repositories.experience_repository import ExperienceRepository, ordered_evidence_ids
from app.experience.schemas.evidence_items import EvidenceRead
from app.experience.schemas.experiences import (
    DeletionImpactResponse,
    ExperienceCreate,
    ExperienceDetail,
    ExperienceListQuery,
    ExperienceListResponse,
    ExperienceRead,
    ExperienceUpdate,
)
from app.experience.services.experience_completeness_service import (
    READY_COMPLETENESS_THRESHOLD,
    calculate_completeness,
)
from app.experience.services.experience_field_service import (
    ExperienceFieldService,
    FieldRevisionConflictError,
)

_NON_NULLABLE_UPDATE_FIELDS = frozenset(
    {"kind", "title", "is_current", "technologies", "tags"}
)


class ExperienceDomainError(Exception):
    """经历库可预期应用错误的基类。"""


class ExperienceNotFoundError(ExperienceDomainError):
    """经历标识符无法找到记录时抛出。"""


class ExperienceConflictError(ExperienceDomainError):
    """其他方面有效的修改与已存储状态冲突时抛出。"""


class ExperienceReadyConflictError(ExperienceConflictError):
    """草稿尚未达到就绪阈值时抛出。"""

    def __init__(self, completeness: int, missing_dimensions: list[str]) -> None:
        super().__init__("Experience is not complete enough to mark ready")
        self.completeness = completeness
        self.missing_dimensions = missing_dimensions


class ExperienceValidationError(ExperienceDomainError):
    """请求解析后的业务规则校验失败时抛出。"""


class ExperienceService:
    """负责经历事务、派生完整度和响应组装。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._experienceRepository = ExperienceRepository(session)
        self._evidenceRepository = EvidenceRepository(session)
        self._fields = ExperienceFieldService(session)

    async def create(self, request: ExperienceCreate) -> ExperienceDetail:
        """创建草稿记录并持久化其权威完整度分数。"""
        fields = request.model_dump()
        fields["kind"] = request.kind.value
        try:
            item = await self._experienceRepository.create(
                ExperienceItem(
                    **fields,
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
        """返回一条记录，并按保存顺序展开其证据。"""
        item = await self._get_or_raise(experience_id)
        return await self._detail(item)

    async def list(self, query: ExperienceListQuery) -> ExperienceListResponse:
        """按仓储搜索和筛选契约列出简要经历记录。"""
        try:
            rows = await self._experienceRepository.list(
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
        """更新可编辑字段，同时校验合并状态并刷新完整度。"""
        fields = request.model_dump(exclude_unset=True)
        expected_field_revisions = fields.pop("expected_field_revisions", {})
        if "kind" in fields and fields["kind"] is not None:
            fields["kind"] = request.kind.value # model_dump()：默认使用 Python 模式，会保留 Enum 对象

        try:
            existing = await self._get_or_raise(experience_id)
            self._reject_null_non_nullable_fields(fields)
            self._validate_merged_dates(existing, fields)
            changed_keys = {
                key for key, value in fields.items() if getattr(existing, key) != value
            }
            await self._fields.claim_experience_units(
                experience_id, expected_field_revisions, changed_keys
            )
            updated = await self._experienceRepository.update_fields(experience_id, fields)
            if changed_keys:
                await self._fields.advance_experience_fields(updated, changed_keys)
            await self._recalculate_completeness(updated)
            detail = await self._detail(updated)
            await self._session.commit()
            return detail
        except FieldRevisionConflictError as error:
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
        """在一个写事务中将完整度达标的活动记录提升为就绪。"""
        try:
            item = await self._get_or_raise(experience_id)
            if item.status == "archived":
                raise ExperienceConflictError(
                    f"Experience {experience_id} is archived; restore it before marking ready"
                )
            guidance = await self._guidance(item)
            if guidance.completeness < READY_COMPLETENESS_THRESHOLD:
                raise ExperienceReadyConflictError(
                    guidance.completeness, guidance.missing_dimensions
                )
            updated = await self._experienceRepository.set_status(experience_id, "ready")
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
        """归档记录，作为可恢复的普通删除生命周期操作。"""
        return await self._transition_status(experience_id, "archived")

    async def restore(self, experience_id: int) -> ExperienceDetail:
        """将已归档记录恢复为草稿，不保留此前的就绪状态。"""
        try:
            item = await self._get_or_raise(experience_id)
            if item.status != "archived":
                raise ExperienceConflictError(
                    f"Experience {experience_id} must be archived before it can be restored"
                )
            updated = await self._experienceRepository.set_status(item.experience_id, "draft")
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
        """返回稳定的删除审查结构，暂不查询未来的匹配或简历关联。"""
        await self._get_or_raise(experience_id)
        return DeletionImpactResponse(affected_matches=[], affected_resumes=[])

    async def permanently_delete(self, experience_id: int) -> None:
        """永久删除已归档记录及其当前拥有的证据。"""
        try:
            item = await self._get_or_raise(experience_id)
            if item.status != "archived":
                raise ExperienceConflictError(
                    f"Experience {experience_id} must be archived before permanent deletion"
                )
            owned_evidence_ids = ordered_evidence_ids(item)
            deleted = await self._experienceRepository.delete(experience_id)
            if not deleted:
                raise ExperienceNotFoundError(f"Experience {experience_id} was not found")
            for evidence_id in owned_evidence_ids:
                await self._evidenceRepository.delete(evidence_id)
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
        """串行执行生命周期写入，避免陈旧操作静默覆盖其他操作。"""
        try:
            await self._get_or_raise(experience_id)
            updated = await self._experienceRepository.set_status(experience_id, target_status)
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
        item = await self._experienceRepository.get(experience_id)
        if item is None:
            raise ExperienceNotFoundError(f"Experience {experience_id} was not found")
        return item

    async def _recalculate_completeness(self, item: ExperienceItem) -> None:
        evidence_items = await self._evidenceRepository.list_for_experience(
            item.experience_id
        )
        result = calculate_completeness(
            item, evidence_items, language=get_content_language()
        )
        updated = await self._experienceRepository.set_completeness(item.experience_id, result.completeness)
        if updated.status == "ready" and result.completeness < READY_COMPLETENESS_THRESHOLD:
            await self._experienceRepository.set_status(item.experience_id, "draft")

    async def _detail(self, item: ExperienceItem) -> ExperienceDetail:
        evidence_items, guidance = await self._evidence_and_guidance(item)
        field_states = await self._fields.list_states(item.experience_id)
        state_payloads = []
        for state in field_states:
            state_payloads.append(
                {
                    "key": state.target_key,
                    "ref_id": state.ref_id or None,
                    "status": state.status,
                    "revision": await self._fields.revision_for_state(state),
                }
            )
        return ExperienceDetail(
            **self._read(item).model_dump(),
            evidence_items=[self._evidence_read(evidence) for evidence in evidence_items],
            missing_dimensions=guidance.missing_dimensions,
            suggested_questions=guidance.suggested_questions,
            field_states=state_payloads,
        )

    async def _guidance(self, item: ExperienceItem):
        """计算实时完整度，不依赖可能陈旧的持久化分数。"""
        _, guidance = await self._evidence_and_guidance(item)
        return guidance

    async def _evidence_and_guidance(self, item: ExperienceItem):
        evidence_items = await self._evidenceRepository.list_for_experience(
            item.experience_id
        )
        return evidence_items, calculate_completeness(
            item, evidence_items, language=get_content_language()
        )

    @staticmethod
    def _validate_merged_dates(item: ExperienceItem, fields: dict[str, Any]) -> None:
        "如果经历仍在进行中，就不能存在结束日期"
        is_current = fields.get("is_current", item.is_current)
        end_date = fields.get("end_date", item.end_date)
        if is_current and end_date is not None:
            raise ExperienceValidationError("current experiences cannot have an end_date")

    @staticmethod
    def _reject_null_non_nullable_fields(fields: dict[str, Any]) -> None:
        "校验必填字段有没有给null"
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
            evidence_ids=ordered_evidence_ids(item),
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
