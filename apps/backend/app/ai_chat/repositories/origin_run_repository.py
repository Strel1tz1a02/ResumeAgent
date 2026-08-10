"""读取记忆模块需要的完整历史 Run。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.memory.runs import OriginRun
from app.ai_chat.models import AiChatMessage, AiChatRun, AiChatToolCall
from app.ai_chat.types import JsonObject

_HISTORY_RUN_STATUSES = ("completed", "failed")


def _message_record(row: AiChatMessage) -> JsonObject:
    """把消息记录转换为历史内容。"""
    return {
        "role": row.role,
        "content": row.content,
        "status": row.status,
    }


def _tool_call_record(row: AiChatToolCall) -> JsonObject:
    """把工具调用转换为历史内容。"""
    return {
        "tool_call_index": row.tool_call_index,
        "tool_name": row.tool_name,
        "arguments": dict(row.arguments),
        "status": row.status,
        "decision": row.decision,
        "result": dict(row.tool_result) if row.tool_result is not None else None,
    }


class OriginRunRepository:
    """读取目标 Run 之前可进入历史上下文的原始 Run。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定调用方提供的数据库会话。"""
        self._session = session

    async def history_before(self, run_id: int) -> list[OriginRun]:
        """读取同一会话中目标 Run 之前的终态 Run。"""
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
        run_models = list(run_result.scalars().all())
        if not run_models:
            return []

        run_ids = [run.id for run in run_models]
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

        return [
            OriginRun(
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
            for run in run_models
        ]
