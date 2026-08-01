"""Run persistence using a caller-owned transaction."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.models import AiChatRun, utcnow_iso


class RunRepository:
    """Manage the single current run for a conversation."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind repository operations to a caller-owned session."""
        self._session = session

    async def create(
        self, *, conversation_id: int, kind: str, tools_enabled: bool
    ) -> AiChatRun:
        """Create and flush a running run."""
        row = AiChatRun(
            conversation_id=conversation_id,
            kind=kind,
            status="running",
            tools_enabled=tools_enabled,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, run_id: int) -> AiChatRun | None:
        """Return a run by ID."""
        return await self._session.get(AiChatRun, run_id)

    async def current(self, conversation_id: int) -> AiChatRun | None:
        """Return the running or suspended run for a conversation."""
        result = await self._session.execute(
            select(AiChatRun).where(
                AiChatRun.conversation_id == conversation_id,
                AiChatRun.status.in_(("running", "suspended")),
            )
        )
        return result.scalar_one_or_none()

    async def set_status(
        self, row: AiChatRun, status: str, error_code: str | None = None
    ) -> None:
        """Set run status and finish terminal runs."""
        row.status = status
        row.error_code = error_code
        row.finished_at = None if status in {"running", "suspended"} else utcnow_iso()
        await self._session.flush()
