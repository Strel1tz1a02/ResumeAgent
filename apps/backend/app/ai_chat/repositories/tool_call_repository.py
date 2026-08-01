"""Tool Call persistence using a caller-owned transaction."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.models import AiChatToolCall, utcnow_iso


class ToolCallRepository:
    """Persist opaque Tool payloads and generic delivery state."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind repository operations to a caller-owned session."""
        self._session = session

    async def create(
        self,
        *,
        conversation_id: int,
        run_id: int,
        provider_tool_call_id: str | None,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> AiChatToolCall:
        """Persist one fully assembled Tool Call."""
        row = AiChatToolCall(
            conversation_id=conversation_id,
            run_id=run_id,
            provider_tool_call_id=provider_tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, tool_call_id: int) -> AiChatToolCall | None:
        """Return a Tool Call by ID."""
        return await self._session.get(AiChatToolCall, tool_call_id)

    async def get_by_resolution_id(
        self, conversation_id: int, client_resolution_id: str
    ) -> AiChatToolCall | None:
        """Find an idempotent proposal resolution."""
        result = await self._session.execute(
            select(AiChatToolCall).where(
                AiChatToolCall.conversation_id == conversation_id,
                AiChatToolCall.client_resolution_id == client_resolution_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_provider_id(
        self, run_id: int, provider_tool_call_id: str
    ) -> AiChatToolCall | None:
        """Find a Tool Call replayed by an interrupt-restarted node."""
        result = await self._session.execute(
            select(AiChatToolCall).where(
                AiChatToolCall.run_id == run_id,
                AiChatToolCall.provider_tool_call_id == provider_tool_call_id,
            )
        )
        return result.scalar_one_or_none()

    async def request_approval(
        self,
        row: AiChatToolCall,
        *,
        proposal_payload: dict[str, Any],
        guard_payload: dict[str, Any],
    ) -> None:
        """Mark a Tool Call as waiting for a user decision."""
        row.proposal_payload = proposal_payload
        row.guard_payload = guard_payload
        row.status = "awaiting_approval"
        row.updated_at = utcnow_iso()
        await self._session.flush()

    async def resolve(
        self,
        row: AiChatToolCall,
        *,
        decision: str | None,
        tool_result: dict[str, Any],
        client_resolution_id: str | None = None,
    ) -> None:
        """Persist an immutable opaque Tool Result."""
        row.status = "resolved"
        row.decision = decision
        row.tool_result = tool_result
        row.delivery_status = "pending"
        row.client_resolution_id = client_resolution_id
        row.resolved_at = utcnow_iso()
        row.updated_at = row.resolved_at
        await self._session.flush()

    async def pending_results(self, conversation_id: int) -> list[AiChatToolCall]:
        """Return Tool Results not yet consumed by a successful model response."""
        result = await self._session.execute(
            select(AiChatToolCall)
            .where(
                AiChatToolCall.conversation_id == conversation_id,
                AiChatToolCall.status == "resolved",
                AiChatToolCall.delivery_status == "pending",
            )
            .order_by(AiChatToolCall.id)
        )
        return list(result.scalars().all())

    async def mark_consumed(self, rows: list[AiChatToolCall]) -> None:
        """Mark Tool Results consumed after a complete model response."""
        now = utcnow_iso()
        for row in rows:
            row.delivery_status = "consumed"
            row.updated_at = now
        await self._session.flush()
