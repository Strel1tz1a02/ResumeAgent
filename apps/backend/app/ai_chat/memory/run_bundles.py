"""把终态 Run 组装成不可拆分的历史单元。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.models import AiChatMessage, AiChatRun, AiChatToolCall
from app.ai_chat.types import JsonObject

_HISTORY_RUN_STATUSES = ("completed", "failed")


@dataclass(frozen=True)
class RunBundle:
    """一个只能整体保留或整体压缩的终态 Run。"""

    run_id: int
    kind: str
    status: str
    error_code: str | None
    messages: tuple[JsonObject, ...]
    tool_calls: tuple[JsonObject, ...]

    def history_record(self) -> JsonObject:
        """返回可序列化进历史 Prompt 的完整 Run 记录。"""
        return {
            "run_id": self.run_id,
            "kind": self.kind,
            "status": self.status,
            "error_code": self.error_code,
            "messages": [dict(message) for message in self.messages],
            "tool_calls": [dict(tool_call) for tool_call in self.tool_calls],
        }

    def stable_hash(self) -> str:
        """绑定压缩结果与 Run 的真实持久化内容。"""
        encoded = json.dumps(
            self.history_record(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _message_record(row: AiChatMessage) -> JsonObject:
    return {
        "role": row.role,
        "content": row.content,
        "status": row.status,
    }


def _tool_call_record(row: AiChatToolCall) -> JsonObject:
    return {
        "tool_call_index": row.tool_call_index,
        "tool_name": row.tool_name,
        "arguments": dict(row.arguments),
        "status": row.status,
        "decision": row.decision,
        "result": dict(row.tool_result) if row.tool_result is not None else None,
    }


class RunBundleBuilder:
    """读取目标 Run 之前的 completed/failed Run。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def history_before(
        self, run_id: int
    ) -> tuple[AiChatRun, list[RunBundle]]:
        boundary = await self._session.get(AiChatRun, run_id)
        if boundary is None:
            raise LookupError(f"run {run_id} does not exist")
        run_result = await self._session.execute(
            select(AiChatRun)
            .where(
                AiChatRun.conversation_id == boundary.conversation_id,
                AiChatRun.id < boundary.id,
                AiChatRun.status.in_(_HISTORY_RUN_STATUSES),
            )
            .order_by(AiChatRun.id)
        )
        runs = list(run_result.scalars().all())
        if not runs:
            return boundary, []

        run_ids = [run.id for run in runs]
        message_result = await self._session.execute(
            select(AiChatMessage)
            .where(AiChatMessage.run_id.in_(run_ids))
            .order_by(AiChatMessage.sequence)
        )
        tool_result = await self._session.execute(
            select(AiChatToolCall)
            .where(AiChatToolCall.run_id.in_(run_ids))
            .order_by(AiChatToolCall.run_id, AiChatToolCall.tool_call_index)
        )
        messages_by_run: dict[int, list[AiChatMessage]] = {
            item: [] for item in run_ids
        }
        tools_by_run: dict[int, list[AiChatToolCall]] = {
            item: [] for item in run_ids
        }
        for row in message_result.scalars().all():
            if row.run_id is not None:
                messages_by_run[row.run_id].append(row)
        for row in tool_result.scalars().all():
            tools_by_run[row.run_id].append(row)

        return boundary, [
            RunBundle(
                run_id=run.id,
                kind=run.kind,
                status=run.status,
                error_code=run.error_code,
                messages=tuple(
                    _message_record(row) for row in messages_by_run[run.id]
                ),
                tool_calls=tuple(
                    _tool_call_record(row) for row in tools_by_run[run.id]
                ),
            )
            for run in runs
        ]
