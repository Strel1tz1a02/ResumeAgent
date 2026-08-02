"""个人经历库的业务 Adapter。"""

from __future__ import annotations

from collections.abc import Mapping

from langgraph.graph import StateGraph

from app import database as database_module
from app.ai_chat.adapters.base import BaseAdapter
from app.ai_chat.runtime import AiChatRuntime
from app.ai_chat.tools.handler import ToolHandler
from app.ai_chat.types import (
    AdapterInput,
    SubjectRef,
    TargetRef,
    ValidatedBinding,
)
from app.experience_ai_chat.context import build_model_messages
from app.experience_ai_chat.graph import ExperienceInputState, build_experience_graph
from app.experience_ai_chat.prompts import system_prompt
from app.experience_ai_chat.tools import ContentChangeHandler
from app.repositories.experience_repository import ExperienceRepository
from app.services.experience_field_service import ExperienceFieldService
from app.services.experience_fields import EXPERIENCE_TARGET_KEYS
from app.services.experience_service import ExperienceService


class ExperienceAdapter(BaseAdapter):
    """把通用会话绑定和输出翻译为经历领域语义。"""

    def __init__(self) -> None:
        """构造无请求状态、可长期复用的 Tool Handler 集合。"""
        handlers: tuple[ToolHandler, ...] = (ContentChangeHandler(),) # ...：可以有任意多个 ToolHandler 类型的元素
        self._handlers = {handler.name: handler for handler in handlers}

    async def validate_binding(
        self, subject: SubjectRef, target: TargetRef
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
            key = target.key
            ref_id = target.ref_id
            if key in EXPERIENCE_TARGET_KEYS:
                if ref_id is not None:
                    raise ValueError("experience field cannot bind evidence id")
            elif key == "evidence":
                if ref_id is not None:
                    raise ValueError("evidence conversation must bind the collection")
            else:
                raise ValueError("unsupported experience target")
            snapshot_key = "evidence_new" if key == "evidence" else key
            await ExperienceFieldService(session).snapshot(
                experience_id, snapshot_key, ref_id
            )

        return ValidatedBinding(
            subject=SubjectRef(type="experience", id=str(experience_id)),
            target=TargetRef(key=target.key, ref_id=target.ref_id),
        )

    async def parse_input(self, value: AdapterInput) -> ExperienceInputState:
        """只读取已保存经历，构造可序列化 Graph State。"""
        experience_id = int(value["subject"]["id"])
        key = str(value["target"]["key"]) # 目标字段的名称
        ref_id_value = value["target"].get("ref_id") # Evidence ID / None
        ref_id = int(ref_id_value) if isinstance(ref_id_value, int) else None
        async with database_module.db.session() as session:
            detail = await ExperienceService(session).get(experience_id)
            snapshot_key = "evidence_new" if key == "evidence" else key
            snapshot = await ExperienceFieldService(session).snapshot(
                experience_id, snapshot_key, ref_id
            )
        detail_json = detail.model_dump(mode="json")
        evidence_revisions = {
            str(state.ref_id): state.revision
            for state in detail.field_states
            if key == "evidence" and state.key == "action" and state.ref_id is not None
        }
        target_value = detail_json["evidence_items"] if key == "evidence" else snapshot.value
        prompt = system_prompt(value["language"], key)
        messages = build_model_messages(
            prompt=prompt,
            detail=detail_json,
            target=dict(value["target"]),
            target_status=snapshot.status,
            target_revision=snapshot.revision,
            history=list(value["messages"]),
            pending=list(value["pending_tool_results"]),
        )
        return ExperienceInputState(
            experience=detail_json,
            target_value=target_value,
            normalized_target_value=target_value,
            target_revision=snapshot.revision,
            target_status=snapshot.status,
            evidence_revisions=evidence_revisions,
            system_prompt=prompt,
            model_messages=messages,
            tools_enabled=value["run_kind"] != "opening" and value["tools_enabled"],
        )

    def build_graph(self, runtime: AiChatRuntime) -> StateGraph:
        """返回由经历业务定义、尚未编译的 Graph。"""
        return build_experience_graph(runtime)

    def get_tool_handlers(self) -> Mapping[str, ToolHandler]:
        """返回经历业务唯一的内容修改 Tool Handler。"""
        return self._handlers
