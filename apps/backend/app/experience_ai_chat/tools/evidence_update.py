"""EvidenceItem 按 ID 局部修改 Tool。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import database as database_module
from app.ai_chat.tools.handler import (
    ApprovalProposal,
    ImmediateToolResult,
    ToolContext,
    ToolHandler,
    ToolResult,
    ToolValidation,
)
from app.services.experience_ai_mutation_service import ExperienceAiMutationService
from app.repositories.experience_repository import ExperienceRepository
from app.services.experience_field_service import ExperienceFieldService
from app.services.experience_fields import EVIDENCE_TARGET_KEYS, normalize_field_value
from app.experience_ai_chat.tools.common import experience_id, generation_guard, target


class EvidenceFields(BaseModel):
    """只允许 Evidence 的三个内容字段。"""

    model_config = ConfigDict(extra="forbid")
    action: str | None = None
    result: str | None = None
    metrics: str | None = None

    @model_validator(mode="after")
    def validate_partial_update(self) -> "EvidenceFields":
        """拒绝空 Patch 以及空 action。"""
        if not self.model_fields_set:
            raise ValueError("updates must contain one field")
        if "action" in self.model_fields_set and not (self.action or "").strip():
            raise ValueError("action cannot be blank")
        return self


class EvidenceUpdateArguments(BaseModel):
    """显式 Evidence ID 与局部字段更新。"""

    model_config = ConfigDict(extra="forbid")
    evidence_id: int = Field(gt=0)
    updates: EvidenceFields


class EvidenceUpdateHandler(ToolHandler):
    """申请修改当前会话绑定的 EvidenceItem 字段。"""

    name = "evidence_update"
    arguments_schema = EvidenceUpdateArguments

    async def validate(
        self, context: ToolContext, arguments: BaseModel
    ) -> ToolValidation:
        """验证 ID 所有权、单字段范围和生成期间 revision。"""
        values = EvidenceUpdateArguments.model_validate(arguments)
        key, ref_id = target(context)
        updates = values.updates.model_dump(mode="json", exclude_unset=True)
        if (
            key not in EVIDENCE_TARGET_KEYS
            or ref_id is None
            or values.evidence_id != ref_id
            or set(updates) != {key}
        ):
            return ImmediateToolResult({"outcome": "invalid_target"})
        start_revision, _ = generation_guard(context)
        async with database_module.db.session() as session:
            item = await ExperienceRepository(session).get(experience_id(context))
            if (
                item is None
                or item.status == "archived"
                or ref_id not in (item.evidence_ids or [])
            ):
                return ImmediateToolResult({"outcome": "invalidated"})
            snapshot = await ExperienceFieldService(session).snapshot(
                experience_id(context), key, ref_id
            )
        if snapshot.revision != start_revision:
            return ImmediateToolResult({"outcome": "invalidated"})
        proposed = normalize_field_value(key, updates[key])
        if proposed == snapshot.normalized_value:
            return ImmediateToolResult({"outcome": "no_change"})
        return ApprovalProposal(
            proposal_payload={
                "operation": self.name,
                "target": {"key": key, "ref_id": ref_id},
                "evidence_id": ref_id,
                "current_values": {key: snapshot.value},
                "updates": {key: proposed},
            },
            guard_payload={
                "experience_id": experience_id(context),
                "operation": self.name,
                "target": {"key": key, "ref_id": ref_id},
                "revision": snapshot.revision,
                "normalized_current_value": snapshot.normalized_value,
            },
        )

    async def resolve(
        self,
        context: ToolContext,
        arguments: BaseModel,
        proposal_payload: dict[str, Any],
        guard_payload: dict[str, Any],
        decision: Literal["approve", "reject"],
    ) -> ToolResult:
        """审批同意后只写 updates 中的目标字段。"""
        if decision == "reject":
            return ToolResult({"outcome": "rejected", "operation": self.name})
        target_payload = guard_payload["target"]
        key = str(target_payload["key"])
        evidence_id = int(target_payload["ref_id"])
        async with database_module.db.session() as session:
            payload = await ExperienceAiMutationService(session).apply_evidence(
                int(guard_payload["experience_id"]),
                evidence_id,
                key,
                proposal_payload["updates"][key],
                expected_revision=int(guard_payload["revision"]),
                expected_value=guard_payload.get("normalized_current_value"),
            )
        return ToolResult(payload)
