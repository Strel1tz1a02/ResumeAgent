"""统一的经历内容修改 Tool 路由。"""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from app import database as database_module
from app.ai_chat.tools.handler import ToolContext, ToolHandler
from app.ai_chat.tools.results import (
    ApprovalProposal,
    ToolInvocationResult,
    ToolResult,
)
from app.experience.adapters.tool_context import (
    evidence_generation_revision,
    experience_id,
    generation_revision,
    scope_field,
)
from app.experience.services.experience_ai_mutation_service import (
    ExperienceAiMutationService,
    PreparedExperienceChange,
)


class ContentChangeScope(BaseModel):
    """模型声明的内容修改目标。"""

    model_config = ConfigDict(extra="forbid")

    field: Literal[
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
    evidence_id: int | None = Field(default=None, gt=0)


class EvidenceContent(BaseModel):
    """模型修改或新增的一条完整 EvidenceItem。"""

    model_config = ConfigDict(extra="forbid") # 不允许出现 Schema 未定义的额外字段

    action: str
    result: str | None
    metrics: str | None


class ContentChangeArguments(BaseModel):
    """模型提交目标与建议内容，具体内容结构由 Service 校验。"""

    model_config = ConfigDict(extra="forbid")

    scope: ContentChangeScope
    suggested_content: str | bool | list[str] | EvidenceContent | None


class ContentChangeHandler(ToolHandler):
    """解析统一参数，并按目标形态路由到经历领域服务。"""

    name = "content_change"
    description = (
        "当前会话目标已经形成有事实依据、可直接保存的明确内容时，申请修改该内容。"
        "普通经历字段的 scope.field 必须等于会话范围且 evidence_id 为空。"
        "Evidence 会话中 scope.field 必须为 evidence：修改已有 EvidenceItem 时必须提交其 evidence_id，"
        "suggested_content 必须包含该 Item 完整的 action、result、metrics；创建时 evidence_id 为空，"
        "新 Item 会追加到列表末尾。一次只能修改或创建一个 EvidenceItem，其他 Item 不会变化。"
        "事实不明确时继续询问；每轮最多调用一次；不要在正文中重复建议内容。"
    )
    arguments_schema = ContentChangeArguments

    async def invoke(self, context: ToolContext, arguments: BaseModel) -> ToolInvocationResult:
        """只负责解析和路由；目标、内容与 revision 均由 Service 校验。"""
        values = cast(ContentChangeArguments, arguments)
        conversation_field = scope_field(context)
        start_revision = generation_revision(context)
        scope = values.scope
        suggested = (
            values.suggested_content.model_dump(mode="json")
            if isinstance(values.suggested_content, BaseModel)
            else values.suggested_content
        )
        async with database_module.db.session() as session:
            service = ExperienceAiMutationService(session)
            if scope.field == "evidence" and scope.evidence_id is None: # 新增证据
                prepared = await service.prepare_evidence_append(
                    experience_id(context),
                    scope.field,
                    scope.evidence_id,
                    suggested,
                    scope_field=conversation_field,
                    expected_revision=start_revision,
                )
            elif scope.field == "evidence": # 修改证据
                prepared = await service.prepare_evidence_change(
                    experience_id(context),
                    scope.field,
                    scope.evidence_id,
                    suggested,
                    scope_field=conversation_field,
                    expected_revision=evidence_generation_revision(
                        context, scope.evidence_id
                    ),
                )
            else: #  修改普通字段
                prepared = await service.prepare_field_change(
                    experience_id(context),
                    scope.field,
                    scope.evidence_id,
                    suggested,
                    scope_field=conversation_field,
                    expected_revision=start_revision,
                )
        if isinstance(prepared, PreparedExperienceChange): # 如果需要审批
            return ApprovalProposal(
                proposal_payload=prepared.proposal_payload,
                guard_payload=prepared.guard_payload,
            )
        return ToolResult(prepared) # 如果无需审批

    async def resolve(
        self,
        context: ToolContext,
        proposal_payload: dict[str, Any],
        guard_payload: dict[str, Any],
        decision: Literal["approve", "reject"],
    ) -> ToolResult:
        """拒绝直接返回；同意后按已校验目标路由到原子写入服务。"""
        if decision == "reject":
            return ToolResult({"outcome": "rejected"})
        scope = dict(guard_payload["scope"])
        field = str(scope["field"])
        evidence_id = scope.get("evidence_id")
        suggested = proposal_payload.get("suggested_content")
        session = context.session
        if session is None:
            raise RuntimeError("tool resolution requires a shared transaction")
        service = ExperienceAiMutationService(session)
        if field == "evidence" and evidence_id is None:
            payload = await service.append_evidence(
                int(guard_payload["experience_id"]),
                dict(suggested),
                expected_revision=int(guard_payload["revision"]),
            )
        elif field == "evidence" and isinstance(evidence_id, int):
            payload = await service.apply_evidence(
                int(guard_payload["experience_id"]),
                evidence_id,
                dict(suggested),
                expected_revision=int(guard_payload["revision"]),
                expected_value=guard_payload.get("normalized_current_content"),
            )
        else:
            payload = await service.apply_field(
                int(guard_payload["experience_id"]),
                field,
                suggested,
                expected_revision=int(guard_payload["revision"]),
                expected_value=guard_payload.get("normalized_current_content"),
            )
        return ToolResult(payload)
