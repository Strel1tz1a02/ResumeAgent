"""把稳定的 completed Run 组装成不可拆分的历史单元。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.models import AiChatMessage, AiChatRun, AiChatToolCall
from app.ai_chat.types import JsonObject


@dataclass(frozen=True)
class RunBundle:
    """一个可整体保留、裁剪或压缩的 completed Run。"""

    run_id: int
    kind: str
    messages: tuple[JsonObject, ...]
    tool_outcomes: tuple[JsonObject, ...]
    first_sequence: int
    last_sequence: int

    def model_messages(self) -> list[JsonObject]:
        """生成中央 Renderer 使用的稳定消息序列。"""
        rendered = [dict(message) for message in self.messages]
        if self.tool_outcomes:
            rendered.append(
                {
                    "role": "assistant",
                    "content": "AI_CHAT_TOOL_OUTCOMES\n"
                    + json.dumps(
                        list(self.tool_outcomes),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\nEND_AI_CHAT_TOOL_OUTCOMES",
                }
            )
        return rendered

    def stable_hash(self) -> str:
        """绑定摘要结果与其真实来源内容。"""
        payload = {
            "run_id": self.run_id,
            "kind": self.kind,
            "messages": self.messages,
            "tool_outcomes": self.tool_outcomes,
            "first_sequence": self.first_sequence,
            "last_sequence": self.last_sequence,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _compact_tool_outcome(row: AiChatToolCall) -> JsonObject:
    result = dict(row.tool_result or {})
    compact_result = {
        key: result[key]
        for key in (
            "outcome",
            "status",
            "revision",
            "collection_revision",
            "changed_ids",
        )
        if key in result
    }
    return {
        "tool_name": row.tool_name,
        "decision": row.decision,
        "result": compact_result,
    }


class RunBundleBuilder:
    """只读取 completed Run，并排除不完整消息残片。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_completed(self, conversation_id: int) -> list[RunBundle]:
        run_result = await self._session.execute(
            select(AiChatRun)
            .where(
                AiChatRun.conversation_id == conversation_id,
                AiChatRun.status == "completed",
            )
            .order_by(AiChatRun.id)
        )
        runs = list(run_result.scalars().all())
        if not runs:
            return []
        run_ids = [run.id for run in runs]
        message_result = await self._session.execute(
            select(AiChatMessage)
            .where(
                AiChatMessage.run_id.in_(run_ids),
                AiChatMessage.status == "completed",
            )
            .order_by(AiChatMessage.sequence)
        )
        tool_result = await self._session.execute(
            select(AiChatToolCall)
            .where(
                AiChatToolCall.run_id.in_(run_ids),
                AiChatToolCall.status == "resolved",
            )
            .order_by(AiChatToolCall.run_id, AiChatToolCall.tool_call_index)
        )
        messages_by_run: dict[int, list[AiChatMessage]] = {run_id: [] for run_id in run_ids}
        tools_by_run: dict[int, list[AiChatToolCall]] = {run_id: [] for run_id in run_ids}
        for row in message_result.scalars().all():
            if row.run_id is not None:
                messages_by_run[row.run_id].append(row)
        for row in tool_result.scalars().all():
            tools_by_run[row.run_id].append(row)

        bundles: list[RunBundle] = []
        for run in runs:
            rows = messages_by_run[run.id]
            users = [row for row in rows if row.role == "user"]
            assistants = [row for row in rows if row.role == "assistant"]
            tools = tools_by_run[run.id]
            stable = (
                run.kind == "opening" and bool(assistants)
            ) or (
                run.kind == "user_turn"
                and bool(users)
                and (bool(assistants) or bool(tools))
            )
            if not stable:
                continue
            visible = tuple(
                {"role": row.role, "content": row.content}
                for row in rows
                if row.role in {"user", "assistant"}
            )
            sequences = [row.sequence for row in rows]
            bundles.append(
                RunBundle(
                    run_id=run.id,
                    kind=run.kind,
                    messages=visible,
                    tool_outcomes=tuple(_compact_tool_outcome(row) for row in tools),
                    first_sequence=min(sequences),
                    last_sequence=max(sequences),
                )
            )
        return bundles
