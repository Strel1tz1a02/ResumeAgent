"""将通用工具上下文解析为经历业务上下文。"""

from __future__ import annotations

from app.ai_chat.tools.types import ToolContext


def experience_id(context: ToolContext) -> int:
    """从服务端校验过的主体中取得经历标识。"""
    if context.subject.get("type") != "experience":
        raise ValueError("invalid experience subject")
    return int(context.subject["id"])


def scope_field(context: ToolContext) -> str:
    """取得经历会话绑定的唯一字段范围。"""
    field = context.scope.get("field")
    if not isinstance(field, str):
        raise ValueError("invalid experience scope field")
    return field


def generation_revision(context: ToolContext) -> int:
    """读取普通字段或证据集合在模型生成开始时的修订号。"""
    snapshot = context.adapter_context.get("revision_snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("missing generation revision snapshot")
    key = "collection_revision" if snapshot.get("scope") == "evidence" else "revision"
    revision = snapshot.get(key)
    if not isinstance(revision, int):
        raise ValueError("missing generation revision")
    return revision


def evidence_generation_revision(context: ToolContext, evidence_id: int) -> int | None:
    """读取共享证据会话生成开始时某一条目的修订号。"""
    snapshot = context.adapter_context.get("revision_snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("scope") != "evidence":
        return None
    revisions = snapshot.get("item_revisions")
    if not isinstance(revisions, dict):
        return None
    revision = revisions.get(str(evidence_id))
    return revision if isinstance(revision, int) else None
