"""使用调用方事务的消息持久化。"""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.models import AiChatMessage, utcnow_iso


class MessageRepository:
    """管理有序的用户和助手消息。"""

    def __init__(self, session: AsyncSession) -> None:
        """将仓储操作绑定到调用方持有的会话。"""
        self._session = session

    async def _next_sequence(self, conversation_id: int) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(AiChatMessage.sequence), 0) + 1).where(
                AiChatMessage.conversation_id == conversation_id
            )
        )
        return int(result.scalar_one())

    async def create(
        self,
        *,
        conversation_id: int,
        run_id: int | None,
        role: str,
        content: str,
        status: str,
        client_message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AiChatMessage:
        """向会话追加一条消息。"""
        row = AiChatMessage(
            conversation_id=conversation_id,
            run_id=run_id,
            sequence=await self._next_sequence(conversation_id),
            role=role,
            content=content,
            status=status,
            client_message_id=client_message_id,
            metadata_json=metadata or {},
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_client_id(
        self, conversation_id: int, client_message_id: str
    ) -> AiChatMessage | None:
        """查找幂等用户消息。"""
        result = await self._session.execute(
            select(AiChatMessage).where(
                AiChatMessage.conversation_id == conversation_id,
                AiChatMessage.client_message_id == client_message_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_completed(self, conversation_id: int) -> list[AiChatMessage]:
        """按稳定顺序返回已完成的可见消息。"""
        result = await self._session.execute(
            select(AiChatMessage)
            .where(
                AiChatMessage.conversation_id == conversation_id,
                AiChatMessage.status == "completed",
            )
            .order_by(AiChatMessage.sequence)
        )
        return list(result.scalars().all())

    async def append(self, row: AiChatMessage, delta: str) -> None:
        """向生成中的助手消息追加流式文本增量。"""
        row.content += delta
        row.updated_at = utcnow_iso()
        await self._session.flush()

    async def finish(self, row: AiChatMessage, status: str) -> None:
        """结束生成中的助手消息。"""
        row.status = status
        row.updated_at = utcnow_iso()
        await self._session.flush()

    async def cancel_generating(self, run_id: int) -> None:
        """取消一次运行中所有仍在生成的助手消息。"""
        result = await self._session.execute(
            select(AiChatMessage).where(
                AiChatMessage.run_id == run_id,
                AiChatMessage.status == "generating",
            )
        )
        for row in result.scalars().all():
            await self.finish(row, "cancelled")
