"""经历字段状态、保存单元 revision 与 AI 原子写入服务。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EvidenceItem, ExperienceFieldState, ExperienceItem
from app.experience.repositories.evidence_repository import EvidenceRepository
from app.experience.repositories.experience_field_state_repository import ExperienceFieldStateRepository
from app.experience.repositories.experience_revision_repository import (
    ExperienceRevisionRepository,
    RevisionConflictError,
)
from app.experience.repositories.experience_repository import ExperienceRepository
from app.experience.services.experience_fields import (
    EVIDENCE_TARGET_KEYS,
    EXPERIENCE_TARGET_KEYS,
    field_status,
    normalize_field_value,
    save_unit_key,
    save_unit_fields,
)


class FieldStateInvariantError(RuntimeError):
    """迁移后本应存在的字段状态缺失。"""


class FieldRevisionConflictError(ValueError):
    """客户端或 AI guard 使用了过期 revision。"""


@dataclass(frozen=True)
class FieldSnapshot:
    """一个会话目标的权威值和并发状态。"""

    key: str
    ref_id: int | None
    value: Any
    normalized_value: Any
    status: str
    revision: int


class ExperienceFieldService:
    """在调用方事务内维护字段状态，不自行提交 Session。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._states = ExperienceFieldStateRepository(session)
        self._revisions = ExperienceRevisionRepository(session)
        self._experiences = ExperienceRepository(session)
        self._evidence = EvidenceRepository(session)

    async def initialize_experience(self, item: ExperienceItem) -> None:
        """为新经历初始化全部经历字段及 Evidence 集合状态。"""
        values = self._experience_values(item)
        for key in EXPERIENCE_TARGET_KEYS:
            await self._states.create(
                item.experience_id, key, field_status(key, values[key], values)
            )
        for unit_key in {save_unit_key(key) for key in EXPERIENCE_TARGET_KEYS}:
            await self._revisions.create(
                item.experience_id, "unit", unit_key
            )
        await self._states.create(
            item.experience_id,
            "evidence_new",
            "complete" if item.evidence_links else "incomplete",
        )
        await self._revisions.create(
            item.experience_id, "collection", "evidence"
        )

    async def initialize_evidence(
        self, experience_id: int, evidence: EvidenceItem
    ) -> None:
        """为追加的 Evidence 初始化三个字段状态。"""
        values = self._evidence_values(evidence)
        for key in EVIDENCE_TARGET_KEYS:
            await self._states.create(
                experience_id,
                key,
                field_status(key, values[key], values),
                ref_id=evidence.id,
            )
        await self._revisions.create(
            experience_id, "unit", "evidence", ref_id=evidence.id
        )

    async def list_states(self, experience_id: int) -> list[ExperienceFieldState]:
        """读取经历详情需要的全部状态。"""
        return await self._states.list_for_experience(experience_id)

    async def revision_for_state(self, row: ExperienceFieldState) -> int:
        """将字段展示状态映射到唯一的保存单元 revision。"""
        if row.target_key == "evidence_new":
            return await self._revisions.current(
                row.experience_id, "collection", "evidence"
            )
        if row.ref_id > 0:
            return await self._revisions.current(
                row.experience_id,
                "unit",
                "evidence",
                ref_id=row.ref_id,
            )
        return await self._revisions.current(
            row.experience_id,
            "unit",
            save_unit_key(row.target_key),
        )

    async def require_state(
        self, experience_id: int, key: str, ref_id: int = 0
    ) -> ExperienceFieldState:
        """读取状态；缺失时报告迁移/领域不变量错误，不临时补建。"""
        row = await self._states.get(experience_id, key, ref_id)
        if row is None:
            raise FieldStateInvariantError(
                f"missing field state: experience={experience_id} key={key} ref_id={ref_id}"
            )
        return row

    async def claim_experience_units(
        self,
        experience_id: int,
        expected: Mapping[str, int],
        changed_keys: set[str],
    ) -> None:
        """按保存单元原子比较并推进手动编辑使用的 revision。"""
        units: dict[str, int] = {}
        for key in changed_keys:
            if key not in expected:
                raise FieldRevisionConflictError(f"missing expected revision for {key}")
            unit_key = save_unit_key(key)
            revision = expected[key]
            if unit_key in units and units[unit_key] != revision:
                raise FieldRevisionConflictError(
                    f"inconsistent expected revisions for save unit {unit_key}"
                )
            units[unit_key] = revision
        for unit_key, revision in sorted(units.items()):
            await self.claim_unit(experience_id, unit_key, revision)

    async def claim_unit(
        self,
        experience_id: int,
        unit_key: str,
        expected_revision: int,
        *,
        ref_id: int = 0,
    ) -> int:
        """原子推进普通字段或 EvidenceItem 共用的数据单元 revision。"""
        try:
            return await self._revisions.claim(
                experience_id,
                "unit",
                unit_key,
                expected_revision,
                ref_id=ref_id,
            )
        except RevisionConflictError as error:
            raise FieldRevisionConflictError(str(error)) from error

    async def claim_collection(
        self, experience_id: int, expected_revision: int
    ) -> int:
        """原子推进 Evidence 集合 revision。"""
        try:
            return await self._revisions.claim(
                experience_id, "collection", "evidence", expected_revision
            )
        except RevisionConflictError as error:
            raise FieldRevisionConflictError(str(error)) from error

    async def current_collection_revision(self, experience_id: int) -> int:
        return await self._revisions.current(
            experience_id, "collection", "evidence"
        )

    async def verify_collection(
        self, experience_id: int, expected_revision: int
    ) -> None:
        """固定全局保存所见的集合结构，不为未变化的集合推进版本。"""
        try:
            await self._revisions.verify(
                experience_id, "collection", "evidence", expected_revision
            )
        except RevisionConflictError as error:
            raise FieldRevisionConflictError(str(error)) from error

    async def advance_experience_fields(
        self, item: ExperienceItem, changed_keys: set[str]
    ) -> None:
        """真实变更后推进所有受影响保存单元成员。"""
        values = self._experience_values(item)
        keys: set[str] = set()
        for changed in changed_keys:
            keys.update(save_unit_fields(changed))
        for key in keys:
            row = await self.require_state(item.experience_id, key)
            await self._states.update_status(
                row, status=field_status(key, values[key], values)
            )

    async def advance_evidence_fields(
        self, experience_id: int, evidence: EvidenceItem
    ) -> None:
        """Evidence 保存单元真实变更后统一推进三个字段。"""
        values = self._evidence_values(evidence)
        for key in EVIDENCE_TARGET_KEYS:
            row = await self.require_state(experience_id, key, evidence.id)
            await self._states.update_status(
                row, status=field_status(key, values[key], values)
            )

    async def advance_collection(self, item: ExperienceItem) -> None:
        """Evidence 新增、删除或重排后推进集合 revision。"""
        row = await self.require_state(item.experience_id, "evidence_new")
        await self._states.update_status(
            row, status="complete" if item.evidence_links else "incomplete"
        )

    async def delete_evidence_states(self, experience_id: int, evidence_id: int) -> None:
        """Evidence 删除时清理其字段状态。"""
        await self._states.delete_targets(
            experience_id, EVIDENCE_TARGET_KEYS, ref_id=evidence_id
        )

    async def snapshot( self, experience_id: int, key: str, ref_id: int | None) -> FieldSnapshot:
        """读取 Adapter/Tool 使用的严格目标快照。"""
        item = await self._experiences.get(experience_id)
        if item is None:
            raise FieldStateInvariantError(f"experience {experience_id} does not exist")
        if key == "evidence_new":
            state = await self.require_state(experience_id, key)
            value: Any = [link.evidence_id for link in item.evidence_links]
            revision = await self._revisions.current(
                experience_id, "collection", "evidence"
            )
            return self._snapshot(key, None, value, state, revision)

        if key in EXPERIENCE_TARGET_KEYS and ref_id is None:
            state = await self.require_state(experience_id, key)
            value = getattr(item, key)
            revision = await self._revisions.current(
                experience_id, "unit", save_unit_key(key)
            )
            return self._snapshot(key, None, value, state, revision)

        if key in EVIDENCE_TARGET_KEYS and ref_id is not None:
            evidence = await self._evidence.get_for_experience(experience_id, ref_id)
            if evidence is None:
                raise FieldStateInvariantError("evidence does not belong to experience")
            state = await self.require_state(experience_id, key, ref_id)
            value = getattr(evidence, key)
            revision = await self._revisions.current(
                experience_id, "unit", "evidence", ref_id=ref_id
            )
            return self._snapshot(key, ref_id, value, state, revision)

        raise FieldStateInvariantError(f"invalid experience target: {key}")

    @staticmethod
    def _snapshot(
        key: str,
        ref_id: int | None,
        value: Any,
        state: ExperienceFieldState,
        revision: int,
    ) -> FieldSnapshot:
        return FieldSnapshot(
            key=key,
            ref_id=ref_id,
            value=value,
            normalized_value=normalize_field_value(key, value),
            status=state.status,
            revision=revision,
        )

    @staticmethod
    def _experience_values(item: ExperienceItem) -> dict[str, Any]:
        return {key: getattr(item, key) for key in EXPERIENCE_TARGET_KEYS}

    @staticmethod
    def _evidence_values(item: EvidenceItem) -> dict[str, Any]:
        return {key: getattr(item, key) for key in EVIDENCE_TARGET_KEYS}
