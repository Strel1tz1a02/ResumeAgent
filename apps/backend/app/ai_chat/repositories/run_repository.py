"""使用调用方事务的运行记录持久化。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.models import AiChatRun, utcnow_iso


class RunRepository:
    """管理会话唯一的当前运行。"""

    def __init__(self, session: AsyncSession) -> None:
        """将仓储操作绑定到调用方持有的会话。"""
        self._session = session

    async def create(
        self, *, conversation_id: int, kind: str, tools_enabled: bool
    ) -> AiChatRun:
        """创建并刷新一条运行中的记录。"""
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
        """根据 ID 返回运行记录。"""
        return await self._session.get(AiChatRun, run_id)

    async def current(self, conversation_id: int) -> AiChatRun | None:
        """返回会话中正在运行或已暂停的运行记录。"""
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
        """设置运行状态，并结束终态运行。"""
        row.status = status
        row.error_code = error_code
        row.finished_at = None if status in {"running", "suspended"} else utcnow_iso()
        await self._session.flush()
