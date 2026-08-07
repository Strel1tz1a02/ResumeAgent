"""使用调用方事务的运行记录持久化。"""

from collections.abc import Collection

from sqlalchemy import select, update
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

    async def transition(
        self,
        run_id: int,
        *,
        from_statuses: Collection[str],
        to_status: str,
        error_code: str | None = None,
    ) -> bool:
        """只允许从声明的来源状态原子转换 Run。"""
        finished_at = (
            None if to_status in {"running", "suspended"} else utcnow_iso()
        )
        result = await self._session.execute(
            update(AiChatRun)
            .where(
                AiChatRun.id == run_id,
                AiChatRun.status.in_(tuple(from_statuses)),
            )
            .values(
                status=to_status,
                error_code=error_code,
                finished_at=finished_at,
            )
        )
        await self._session.flush()
        return result.rowcount == 1
