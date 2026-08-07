"""统一的经历内容修改 Tool 路由。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app import database as database_module
from app.ai_chat.tools.handler import ToolContext, ToolHandler
from app.ai_chat.tools.results import (
    ApprovalProposal,
    ImmediateToolResult,
    ToolResult,
    ToolValidation,
)
from app.experience.tools.common import (
    evidence_generation_revision,
    experience_id,
    generation_revision,
    target,
)
from app.experience.services.experience_ai_mutation_service import (
    ExperienceAiMutationService,
    PreparedExperienceChange,
)


class ContentChangeTarget(BaseModel):
    """模型声明的内容修改目标。"""

    model_config = ConfigDict(extra="forbid")

    key: Literal[
        "kind",
        "title",
        "organization",
        "role",
        "location",
        "start_date",
        "end_date",
        "is_current",
        "background",
        "technologies",
        "tags",
        "notes",
        "evidence",
    ]
    evidence_id: int | None = Field(
        validation_alias=AliasChoices("evidence_id", "ref_id")
    )


class EvidenceContent(BaseModel):
    """模型修改或新增的一条完整 EvidenceItem。"""

    model_config = ConfigDict(extra="forbid")

    action: str
    result: str | None
    metrics: str | None


class ContentChangeArguments(BaseModel):
    """模型提交目标与建议内容，具体内容结构由 Service 校验。"""

    model_config = ConfigDict(extra="forbid")

    target: ContentChangeTarget
    suggested_content: str | bool | list[str] | EvidenceContent | None


class ContentChangeHandler(ToolHandler):
    """解析统一参数，并按目标形态路由到经历领域服务。"""

    name = "content_change"
    description = (
        "当前会话目标已经形成有事实依据、可直接保存的明确内容时，申请修改该内容。"
        "普通经历字段的 target.key 必须等于会话目标且 evidence_id 为空。"
        "Evidence 会话中 target.key 必须为 evidence：修改已有 EvidenceItem 时必须提交其 evidence_id，"
        "suggested_content 必须包含该 Item 完整的 action、result、metrics；创建时 evidence_id 为空，"
        "新 Item 会追加到列表末尾。一次只能修改或创建一个 EvidenceItem，其他 Item 不会变化。"
        "事实不明确时继续询问；每轮最多调用一次；不要在正文中重复建议内容。"
    )
    arguments_schema = ContentChangeArguments

    async def invoke(self, context: ToolContext, arguments: BaseModel ) -> ToolValidation:
        """只负责解析和路由；目标、内容与 revision 均由 Service 校验。"""
        values = ContentChangeArguments.model_validate(arguments)
        bound_key, bound_ref_id = target(context)
        start_revision = generation_revision(context)
        requested = values.target
        suggested = (
            values.suggested_content.model_dump(mode="json")
            if isinstance(values.suggested_content, BaseModel)
            else values.suggested_content
        )
        async with database_module.db.session() as session:
            service = ExperienceAiMutationService(session)
            if bound_key == "evidence" and requested.evidence_id is None:
                prepared = await service.prepare_evidence_append(
                    experience_id(context),
                    requested.key,
                    requested.evidence_id,
                    suggested,
                    bound_key=bound_key,
                    bound_ref_id=bound_ref_id,
                    expected_revision=start_revision,
                )
            elif bound_key == "evidence":
                prepared = await service.prepare_evidence_change(
                    experience_id(context),
                    requested.key,
                    requested.evidence_id,
                    suggested,
                    bound_key=bound_key,
                    bound_ref_id=bound_ref_id,
                    expected_revision=evidence_generation_revision(
                        context, requested.evidence_id
                    ),
                )
            else:
                prepared = await service.prepare_field_change(
                    experience_id(context),
                    requested.key,
                    requested.evidence_id,
                    suggested,
                    bound_key=bound_key,
                    bound_ref_id=bound_ref_id,
                    expected_revision=start_revision,
                )
        if isinstance(prepared, PreparedExperienceChange):
            return ApprovalProposal(
                proposal_payload=prepared.proposal_payload,
                guard_payload=prepared.guard_payload,
            )
        return ImmediateToolResult(prepared)

    async def resolve(
        self,
        context: ToolContext,
        arguments: BaseModel,
        proposal_payload: dict[str, Any],
        guard_payload: dict[str, Any],
        decision: Literal["approve", "reject"],
    ) -> ToolResult:
        """拒绝直接返回；同意后按已校验目标路由到原子写入服务。"""
        if decision == "reject":
            return ToolResult(
                {"outcome": "rejected", "operation": "content_change"}
            )
        target_payload = dict(guard_payload["target"])
        key = str(target_payload["key"])
        ref_id = target_payload.get("ref_id")
        suggested = proposal_payload.get("suggested_content")
        session = context.session
        if session is None:
            raise RuntimeError("tool resolution requires a shared transaction")
        service = ExperienceAiMutationService(session)
        if key == "evidence" and ref_id is None:
            payload = await service.append_evidence(
                    int(guard_payload["experience_id"]),
                    dict(suggested),
                    expected_revision=int(guard_payload["revision"]),
                )
        elif key == "evidence" and isinstance(ref_id, int):
            payload = await service.apply_evidence(
                    int(guard_payload["experience_id"]),
                    ref_id,
                    dict(suggested),
                    expected_revision=int(guard_payload["revision"]),
                    expected_value=guard_payload.get(
                        "normalized_current_content"
                    ),
                )
        else:
            payload = await service.apply_field(
                    int(guard_payload["experience_id"]),
                    key,
                    suggested,
                    expected_revision=int(guard_payload["revision"]),
                    expected_value=guard_payload.get(
                        "normalized_current_content"
                    ),
            )
        return ToolResult(payload)
