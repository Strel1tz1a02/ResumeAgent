"""经历内容修改 Tool 的上下文解析辅助函数。"""

from __future__ import annotations

from typing import Any

from app.ai_chat.tools.handler import ToolContext


def experience_id(context: ToolContext) -> int:
    """从服务端校验过的 subject 中取得经历 ID。"""
    if context.subject.get("type") != "experience":
        raise ValueError("invalid experience subject")
    return int(context.subject["id"])


def target(context: ToolContext) -> tuple[str, int | None]:
    """从会话绑定中取得不可由模型修改的目标。"""
    key = context.target.get("key")
    ref_id = context.target.get("ref_id")
    if not isinstance(key, str):
        raise ValueError("invalid target key")
    if ref_id is not None and not isinstance(ref_id, int):
        raise ValueError("invalid target ref_id")
    return key, ref_id


def generation_guard(context: ToolContext) -> tuple[int, Any]:
    """读取 Graph 在模型生成开始时捕获的 revision 与规范化值。"""
    revision = context.adapter_context.get("target_revision_at_generation_start")
    if not isinstance(revision, int):
        raise ValueError("missing generation revision")
    return revision, context.adapter_context.get(
        "normalized_target_value_at_generation_start"
    )


def evidence_generation_revision(
    context: ToolContext, evidence_id: int
) -> int | None:
    """读取共享 Evidence 会话生成开始时某一 Item 的 revision。"""
    revisions = context.adapter_context.get(
        "evidence_revisions_at_generation_start"
    )
    if not isinstance(revisions, dict):
        return None
    revision = revisions.get(str(evidence_id))
    return revision if isinstance(revision, int) else None
