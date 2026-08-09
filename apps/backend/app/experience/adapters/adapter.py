"""个人经历库的业务 Adapter。"""

from __future__ import annotations

from collections.abc import Mapping

from langgraph.graph import StateGraph

from app import database as database_module
from app.ai_chat.adapters.base import BaseAdapter
from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.tools.handler import ToolHandler
from app.ai_chat.graph.state import AdapterInput
from app.ai_chat.types import ScopeRef, SubjectRef, ValidatedBinding
from app.experience.graph.context import build_model_messages
from app.experience.graph import ExperienceState, build_experience_graph
from app.experience.prompts.ai_chat import system_prompt
from app.experience.schemas.ai_chat import ExperienceChatScope
from app.experience.tools import ContentChangeHandler
from app.experience.repositories.experience_repository import ExperienceRepository
from app.experience.services.experience_field_service import ExperienceFieldService
from app.experience.services.experience_fields import EXPERIENCE_TARGET_KEYS
from app.experience.services.experience_service import ExperienceService


class ExperienceAdapter(BaseAdapter):
    """把通用会话绑定和输出翻译为经历领域语义。"""

    def __init__(self) -> None:
        """构造无请求状态、可长期复用的 Tool Handler 集合。"""
        handlers: tuple[ToolHandler, ...] = (ContentChangeHandler(),) # ...：可以有任意多个 ToolHandler 类型的元素
        self._handlers = {handler.name: handler for handler in handlers}

    async def validate_binding(
        self, subject: SubjectRef, scope: ScopeRef
    ) -> ValidatedBinding:
        """校验能否创建一个绑定到指定业务对象和目标字段的会话"""
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
            snapshot_key = "evidence_new" if field == "evidence" else field
            await ExperienceFieldService(session).snapshot(
                experience_id, snapshot_key, None
            )

        return ValidatedBinding(
            subject=SubjectRef(type="experience", id=str(experience_id)),
            scope=ScopeRef.model_validate(experience_scope.model_dump(mode="json")),
        )

    async def parse_input(self, value: AdapterInput) -> ExperienceState:
        """把统一输入和已保存经历转换成完整经历 Graph State。"""
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
        messages = build_model_messages(
            prompt=prompt,
            detail=detail_json,
            scope=dict(value["scope"]),
            scope_status=snapshot.status,
            scope_revision=snapshot.revision,
            history=list(value["messages"]),
            pending=list(value["pending_tool_results"]),
        )
        return ExperienceState(
            conversation_id=value["conversation_id"],
            run_id=value["run_id"],
            subject=value["subject"],
            scope=value["scope"],
            run_kind=value["run_kind"],
            tools_enabled=value["tools_enabled"],
            revision_snapshot=revision_snapshot,
            model_messages=messages,
            tool_call=None,
            tool_call_id=None,
            tool_phase=None,
            tool_security=None,
            tool_finished=False,
            proposal_id=None,
            approval=None,
        )

    def build_graph(self, runtime: AiChatRuntime) -> StateGraph:
        """返回由经历业务定义、尚未编译的 Graph。"""
        return build_experience_graph(runtime)

    def get_tool_handlers(self) -> Mapping[str, ToolHandler]:
        """返回经历业务唯一的内容修改 Tool Handler。"""
        return self._handlers
