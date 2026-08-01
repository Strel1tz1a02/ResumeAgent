"""EvidenceItem 末尾追加 Tool。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
from app.experience_ai_chat.tools.common import experience_id, generation_guard, target


class EvidenceAppendItem(BaseModel):
    """新 Evidence 的完整内容，不接受 ID 或顺序参数。"""

    model_config = ConfigDict(extra="forbid")
    action: str = Field(min_length=1)
    result: str | None = None
    metrics: str | None = None

    @field_validator("action")
    @classmethod
    def normalize_action(cls, value: str) -> str:
        """新证据必须包含非空行动。"""
        value = value.strip()
        if not value:
            raise ValueError("action cannot be blank")
        return value


class EvidenceAppendArguments(BaseModel):
    """末尾追加 Tool 参数。"""

    model_config = ConfigDict(extra="forbid")
    item: EvidenceAppendItem


class EvidenceAppendHandler(ToolHandler):
    """申请在当前经历 Evidence 列表末尾创建一项。"""

    name = "evidence_append"
    arguments_schema = EvidenceAppendArguments

    async def validate(
        self, context: ToolContext, arguments: BaseModel
    ) -> ToolValidation:
        """验证虚拟集合目标及模型生成期间 collection revision。"""
        values = EvidenceAppendArguments.model_validate(arguments)
        key, ref_id = target(context)
        if key != "evidence_new" or ref_id is not None:
            return ImmediateToolResult({"outcome": "invalid_target"})
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
        item = values.item.model_dump(mode="json")
        return ApprovalProposal(
            proposal_payload={
                "operation": self.name,
                "target": {"key": key, "ref_id": None},
                "item": item,
            },
            guard_payload={
                "experience_id": experience_id(context),
                "operation": self.name,
                "target": {"key": key, "ref_id": None},
                "collection_revision": snapshot.revision,
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
        """审批同意后由数据库生成 ID 并原子追加到末尾。"""
        if decision == "reject":
            return ToolResult({"outcome": "rejected", "operation": self.name})
        async with database_module.db.session() as session:
            payload = await ExperienceAiMutationService(session).append_evidence(
                int(guard_payload["experience_id"]),
                dict(proposal_payload["item"]),
                expected_revision=int(guard_payload["collection_revision"]),
            )
        return ToolResult(payload)
