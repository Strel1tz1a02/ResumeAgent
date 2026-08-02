"""使用调用方事务的会话持久化。"""

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.models import AiChatConversation, utcnow_iso


class ConversationRepository:
    """读取和修改 AI 对话会话，但不自行提交。"""

    def __init__(self, session: AsyncSession) -> None:
        """将仓储操作绑定到调用方持有的会话。"""
        self._session = session

    async def create(
        self,
        *,
        adapter: str,
        subject: dict[str, Any],
        target: dict[str, Any],
        language: str,
    ) -> AiChatConversation:
        """持久化新的使用中会话，并回填整数 ID。"""
        row = AiChatConversation(
            adapter=adapter,
            subject=subject,
            target=target,
            language=language,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, conversation_id: int) -> AiChatConversation | None:
        """根据 ID 返回会话。"""
        return await self._session.get(AiChatConversation, conversation_id)

    async def end(self, row: AiChatConversation, reason: str) -> AiChatConversation:
        """幂等结束使用中的会话。"""
        if row.status != "ended":
            now = utcnow_iso()
            row.status = "ended"
            row.end_reason = reason
            row.ended_at = now
            row.updated_at = now
            await self._session.flush()
        return row

    async def delete(self, conversation_id: int) -> bool:
        """删除一个会话及其外键依赖记录。"""
        result = await self._session.execute(
            delete(AiChatConversation).where(AiChatConversation.id == conversation_id)
        )
        return bool(result.rowcount)

    async def ids_for_subject(
        self, adapter: str, subject: dict[str, Any]
    ) -> list[int]:
        """返回绑定到不透明业务主体的会话 ID。"""
        result = await self._session.execute(
            select(AiChatConversation.id).where(
                AiChatConversation.adapter == adapter,
                AiChatConversation.subject == subject,
            )
        )
        return list(result.scalars().all())
