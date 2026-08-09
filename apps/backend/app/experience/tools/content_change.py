"""统一的经历内容修改工具路由。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.tools.types import (
    ToolContext,
    ToolResult,
)
from app.ai_chat.tools.handler import ToolHandler
from app.ai_chat.tools.security import ToolSecurity
from app.ai_chat.types import JsonObject
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
    """模型修改或新增的一条完整证据。"""

    # 不允许出现参数模式未定义的额外字段。
    model_config = ConfigDict(extra="forbid")

    action: str
    result: str | None
    metrics: str | None


class ContentChangeArguments(BaseModel):
    """模型提交目标与建议内容，具体内容结构由服务校验。"""

    model_config = ConfigDict(extra="forbid")

    scope: ContentChangeScope
    suggested_content: str | bool | list[str] | EvidenceContent | None


class ContentChangeHandler(ToolHandler):
    """解析统一参数，并按目标形态路由到经历领域服务。"""

    name = "content_change"
    security = ToolSecurity.MEDIUM
    description = (
        "当前会话目标已经形成有事实依据、可直接保存的明确内容时，申请修改该内容。"
        "普通经历字段的 scope.field 必须等于会话范围且 evidence_id 为空。"
        "Evidence 会话中 scope.field 必须为 evidence：修改已有 EvidenceItem 时必须提交其 evidence_id，"
        "suggested_content 必须包含该 Item 完整的 action、result、metrics；创建时 evidence_id 为空，"
        "新 Item 会追加到列表末尾。一次只能修改或创建一个 EvidenceItem，其他 Item 不会变化。"
        "事实不明确时继续询问；每轮最多调用一次；不要在正文中重复建议内容。"
    )
    arguments_schema = ContentChangeArguments

    async def validation(
        self,
        context: ToolContext,
        arguments: JsonObject,
    ) -> tuple[JsonObject, JsonObject] | ToolResult:
        """校验模型输入，并生成审批展示和可信的版本保护数据。"""
        try:
            values = ContentChangeArguments.model_validate(arguments)
        except ValidationError as exc:
            raise ToolProtocolError("Invalid arguments for tool content_change") from exc
        session = context.session
        if session is None:
            raise RuntimeError("tool validation requires a shared transaction")
        conversation_field = scope_field(context)
        start_revision = generation_revision(context)
        scope = values.scope
        suggested = (
            values.suggested_content.model_dump(mode="json")
            if isinstance(values.suggested_content, BaseModel)
            else values.suggested_content
        )
        service = ExperienceAiMutationService(session)
        if scope.field == "evidence" and scope.evidence_id is None:
            prepared = await service.prepare_evidence_append(
                experience_id(context),
                scope.field,
                scope.evidence_id,
                suggested,
                scope_field=conversation_field,
                expected_revision=start_revision,
            )
        elif scope.field == "evidence":
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
        else:
            prepared = await service.prepare_field_change(
                experience_id(context),
                scope.field,
                scope.evidence_id,
                suggested,
                scope_field=conversation_field,
                expected_revision=start_revision,
            )
        if isinstance(prepared, PreparedExperienceChange):
            return prepared.proposal_payload, prepared.guard_payload
        return self.show_result(prepared)

    async def execute(
        self,
        context: ToolContext,
        proposal_payload: JsonObject,
        guard_payload: JsonObject,
    ) -> ToolResult:
        """按已校验目标执行原子写入，并在内部整理结果。"""
        scope = dict(guard_payload["scope"])
        field = str(scope["field"])
        evidence_id = scope.get("evidence_id")
        suggested = proposal_payload.get("suggested_content")
        session = context.session
        if session is None:
            raise RuntimeError("tool execution requires a shared transaction")
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
        return self.show_result(payload)

    def show_result(self, payload: JsonObject) -> ToolResult:
        """保留稳定结果标记，并统一封装经历工具结果。"""
        return ToolResult(dict(payload))
