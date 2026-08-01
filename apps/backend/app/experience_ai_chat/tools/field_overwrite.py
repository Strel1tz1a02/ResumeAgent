"""ExperienceItem 单字段覆盖 Tool。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app import database as database_module
from app.ai_chat.tools.handler import (
    ApprovalProposal,
    ImmediateToolResult,
    ToolContext,
    ToolHandler,
    ToolResult,
    ToolValidation,
)
from app.schemas.experiences import ExperienceUpdate
from app.repositories.experience_repository import ExperienceRepository
from app.services.experience_ai_mutation_service import ExperienceAiMutationService
from app.services.experience_field_service import ExperienceFieldService
from app.services.experience_fields import EXPERIENCE_TARGET_KEYS, normalize_field_value
from app.experience_ai_chat.tools.common import experience_id, generation_guard, target


class FieldOverwriteArguments(BaseModel):
    """模型只提供目标字段的新值，字段名来自会话绑定。"""

    model_config = ConfigDict(extra="forbid")
    proposed_value: Any


class FieldOverwriteHandler(ToolHandler):
    """申请覆盖当前会话绑定的经历字段。"""

    name = "field_overwrite"
    arguments_schema = FieldOverwriteArguments

    async def validate(
        self, context: ToolContext, arguments: BaseModel
    ) -> ToolValidation:
        """校验目标、类型和生成期间 revision 后创建审批提案。"""
        values = FieldOverwriteArguments.model_validate(arguments)
        key, ref_id = target(context)
        if key not in EXPERIENCE_TARGET_KEYS or ref_id is not None:
            return ImmediateToolResult({"outcome": "invalid_target"})
        try:
            parsed = ExperienceUpdate.model_validate({key: values.proposed_value})
            proposed = parsed.model_dump(mode="json", exclude_unset=True)[key]
        except Exception:
            return ImmediateToolResult({"outcome": "invalid_value"})
        start_revision, _ = generation_guard(context)
        async with database_module.db.session() as session:
            item = await ExperienceRepository(session).get(experience_id(context))
            if item is None or item.status == "archived":
                return ImmediateToolResult({"outcome": "invalidated"})
            snapshot = await ExperienceFieldService(session).snapshot(
                experience_id(context), key, None
            )
        if snapshot.revision != start_revision:
            return ImmediateToolResult({"outcome": "invalidated"})
        normalized = normalize_field_value(key, proposed)
        if normalized == snapshot.normalized_value:
            return ImmediateToolResult({"outcome": "no_change"})
        return ApprovalProposal(
            proposal_payload={
                "operation": self.name,
                "target": {"key": key, "ref_id": None},
                "current_value": snapshot.value,
                "proposed_value": proposed,
            },
            guard_payload={
                "experience_id": experience_id(context),
                "operation": self.name,
                "target": {"key": key, "ref_id": None},
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
        """拒绝不写业务数据；同意时由领域服务执行二次 guard。"""
        if decision == "reject":
            return ToolResult({"outcome": "rejected", "operation": self.name})
        async with database_module.db.session() as session:
            payload = await ExperienceAiMutationService(session).apply_field(
                int(guard_payload["experience_id"]),
                str(guard_payload["target"]["key"]),
                proposal_payload.get("proposed_value"),
                expected_revision=int(guard_payload["revision"]),
                expected_value=guard_payload.get("normalized_current_value"),
            )
        return ToolResult(payload)
