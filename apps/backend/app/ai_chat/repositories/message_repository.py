"""Message persistence using a caller-owned transaction."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.models import AiChatMessage, utcnow_iso


class MessageRepository:
    """Manage ordered user and assistant messages."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind repository operations to a caller-owned session."""
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
        """Append one message to a conversation."""
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
        """Find an idempotent user message."""
        result = await self._session.execute(
            select(AiChatMessage).where(
                AiChatMessage.conversation_id == conversation_id,
                AiChatMessage.client_message_id == client_message_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_completed(self, conversation_id: int) -> list[AiChatMessage]:
        """Return completed visible messages in stable order."""
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
        """Append a streamed text delta to a generating assistant message."""
        row.content += delta
        row.updated_at = utcnow_iso()
        await self._session.flush()

    async def finish(self, row: AiChatMessage, status: str) -> None:
        """Finish a generating assistant message."""
        row.status = status
        row.updated_at = utcnow_iso()
        await self._session.flush()

    async def cancel_generating(self, run_id: int) -> None:
        """Cancel every still-generating assistant message for one run."""
        result = await self._session.execute(
            select(AiChatMessage).where(
                AiChatMessage.run_id == run_id,
                AiChatMessage.status == "generating",
            )
        )
        for row in result.scalars().all():
            await self.finish(row, "cancelled")
