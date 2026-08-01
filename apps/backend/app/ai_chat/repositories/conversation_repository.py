"""Conversation persistence using a caller-owned transaction."""

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.models import AiChatConversation, utcnow_iso


class ConversationRepository:
    """Read and mutate AI Chat conversations without committing."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind repository operations to a caller-owned session."""
        self._session = session

    async def create(
        self,
        *,
        adapter: str,
        subject: dict[str, Any],
        target: dict[str, Any],
        language: str,
    ) -> AiChatConversation:
        """Persist a new active conversation and populate its integer ID."""
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
        """Return a conversation by ID."""
        return await self._session.get(AiChatConversation, conversation_id)

    async def end(self, row: AiChatConversation, reason: str) -> AiChatConversation:
        """End an active conversation idempotently."""
        if row.status != "ended":
            now = utcnow_iso()
            row.status = "ended"
            row.end_reason = reason
            row.ended_at = now
            row.updated_at = now
            await self._session.flush()
        return row

    async def delete(self, conversation_id: int) -> bool:
        """Delete one conversation and its foreign-key dependants."""
        result = await self._session.execute(
            delete(AiChatConversation).where(AiChatConversation.id == conversation_id)
        )
        return bool(result.rowcount)

    async def ids_for_subject(
        self, adapter: str, subject: dict[str, Any]
    ) -> list[int]:
        """Return conversation IDs bound to an opaque business subject."""
        result = await self._session.execute(
            select(AiChatConversation.id).where(
                AiChatConversation.adapter == adapter,
                AiChatConversation.subject == subject,
            )
        )
        return list(result.scalars().all())
