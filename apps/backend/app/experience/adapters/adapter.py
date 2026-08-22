"""个人经历库的业务适配器。"""

from __future__ import annotations

from collections.abc import Mapping

from langgraph.graph import StateGraph

from app import database as database_module
from app.ai_chat.adapters.base import BaseAdapter
from app.ai_chat.context import ModelContext
from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.tools.approval import ToolApprovalPolicy, ToolRisk
from app.ai_chat.tools.operation import RegisteredTool
from app.ai_chat.types import AdapterInput, JsonObject, ScopeRef, SubjectRef, ValidatedBinding
from app.experience.graph import ExperienceState, build_experience_graph
from app.experience.prompts.ai_chat import system_prompt
from app.experience.repositories.experience_repository import ExperienceRepository
from app.experience.schemas.ai_chat import ExperienceChatScope
from app.experience.services.experience_field_service import ExperienceFieldService
from app.experience.services.experience_fields import EXPERIENCE_TARGET_KEYS
from app.experience.services.experience_service import ExperienceService
from app.experience.tools import ContentChangeOperation


def _content_change_proposal(data: JsonObject) -> JsonObject:
    """从准备数据中选择允许展示给审批界面的修改信息。"""
    return {
        "scope": data.get("scope"),
        "current_content": data.get("current_content"),
        "suggested_content": data.get("suggested_content"),
    }


class ExperienceAdapter(BaseAdapter[ExperienceState]):
    """把通用会话绑定和输出翻译为经历领域语义。"""

    def __init__(self) -> None:
        """构造无请求状态、可长期复用的工具处理器集合。"""
        tool = RegisteredTool(ContentChangeOperation())
        self._tools = {tool.name: tool}
        self._approval = ToolApprovalPolicy(
            {tool.name: ToolRisk.MEDIUM},
            {tool.name: _content_change_proposal},
        )

    async def validate_request(
        self, subject: SubjectRef, scope: ScopeRef
    ) -> ValidatedBinding:
        """检查指定经历及字段当前是否允许启用会话。"""
        if subject.type != "experience":
            raise ValueError("ExperienceAdapter only accepts experience subjects")
        try:
            experience_id = int(subject.id)
        except ValueError as error:
            raise ValueError("invalid experience id") from error
        async with database_module.db.session() as session:
            item = await ExperienceRepository(session).get(experience_id)
            if item is None:
                raise ValueError("experience does not exist")
            if item.status == "archived":
                raise ValueError("archived experience cannot start a conversation")
            experience_scope = ExperienceChatScope.model_validate(
                scope.model_dump(mode="json")
            )
            field = experience_scope.field
            if field not in EXPERIENCE_TARGET_KEYS and field != "evidence":
                raise ValueError("unsupported experience scope")

        return ValidatedBinding(
            subject=SubjectRef(type="experience", id=str(experience_id)),
            scope=ScopeRef.model_validate(experience_scope.model_dump(mode="json")),
        )

    async def parse_input(self, value: AdapterInput) -> ExperienceState:
        """把统一输入和已保存经历转换成完整的经历图状态。"""
        experience_id = int(value["subject"]["id"])
        field = str(value["scope"]["field"])
        async with database_module.db.session() as session:
            detail = await ExperienceService(session).get(experience_id)
            snapshot_key = "evidence_new" if field == "evidence" else field
            snapshot = await ExperienceFieldService(session).snapshot(
                experience_id, snapshot_key, None
            )
        detail_json = detail.model_dump(mode="json")
        evidence_revisions = {
            str(state.ref_id): state.revision
            for state in detail.field_states
            if field == "evidence" and state.key == "action" and state.ref_id is not None
        }
        revision_snapshot = (
            {
                "scope": "evidence",
                "collection_revision": snapshot.revision,
                "item_revisions": evidence_revisions,
            }
            if field == "evidence"
            else {"scope": "field", "revision": snapshot.revision}
        )
        prompt = system_prompt(value["language"], field)
        model_context: ModelContext = {
            "instructions": prompt,
            "domain_sections": [
                {
                    "name": "saved_experience",
                    "data": {
                        "experience": detail_json,
                        # ref_id 只用于前端和持久化绑定，不暴露为模型参数。
                        "scope": {"field": value["scope"].get("field")},
                        "scope_status": snapshot.status,
                        "scope_revision": snapshot.revision,
                    },
                }
            ],
            "messages": list(value["messages"]),
            "pending_tool_results": list(value["pending_tool_results"]),
        }
        return ExperienceState(
            conversation_id=value["conversation_id"],
            run_id=value["run_id"],
            subject=value["subject"],
            scope=value["scope"],
            run_kind=value["run_kind"],
            tools_enabled=value["tools_enabled"],
            revision_snapshot=revision_snapshot,
            model_context=model_context,
            raw_tool_call=None,
            tool_call=None,
        )

    def build_graph(self, runtime: AiChatRuntime) -> StateGraph:
        """返回由经历业务定义、尚未编译的图。"""
        return build_experience_graph(runtime)

    def get_tools(self) -> Mapping[str, RegisteredTool]:
        """返回经历业务唯一的内容修改工具。"""
        return self._tools

    def get_tool_approval_policy(self) -> ToolApprovalPolicy:
        return self._approval
