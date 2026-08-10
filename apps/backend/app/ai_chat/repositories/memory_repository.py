"""使用调用方事务维护按 Run 占位的会话记忆。"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.memory.runs import Memory
from app.ai_chat.models import AiChatRunMemory


class MemoryRepository:
    """维护压缩占位与累计记忆结果，不负责执行控制。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定调用方提供的数据库会话。"""
        self._session = session

    async def get_by_run_id(self, run_id: int) -> AiChatRunMemory | None:
        """按 Run ID 查询一条记忆记录。"""
        result = await self._session.execute(
            select(AiChatRunMemory).where(AiChatRunMemory.run_id == run_id)
        )
        return result.scalar_one_or_none()

    async def get_by_run_ids(
        self,
        run_ids: list[int],
    ) -> dict[int, AiChatRunMemory]:
        """批量查询并按 Run ID 索引记忆记录。"""
        if not run_ids:
            return {}
        result = await self._session.execute(
            select(AiChatRunMemory).where(AiChatRunMemory.run_id.in_(run_ids))
        )
        return {row.run_id: row for row in result.scalars().all()}

    async def create_placeholder(
        self,
        *,
        run_id: int,
    ) -> AiChatRunMemory:
        """在 LLM 调用前幂等写入 pending 占位。"""
        statement = (
            sqlite_insert(AiChatRunMemory)
            .values(
                run_id=run_id,
                status="pending",
                core={},
                other={},
                memory_token_count=0,
            )
            .on_conflict_do_nothing(index_elements=["run_id"])
        )
        await self._session.execute(statement)
        await self._session.flush()
        row = await self.get_by_run_id(run_id)
        if row is None:
            raise RuntimeError("memory placeholder was not persisted")
        return row

    async def complete(
        self,
        *,
        memory_id: int,
        memory: Memory,
    ) -> bool:
        """写入完整校验后的记忆结果。"""
        result = await self._session.execute(
            update(AiChatRunMemory)
            .where(AiChatRunMemory.id == memory_id)
            .values(
                status="completed",
                core=memory.core_json(),
                other=memory.other,
                memory_token_count=memory.token_count,
                error_message=None,
            )
        )
        await self._session.flush()
        return result.rowcount == 1

    async def fail(self, *, memory_id: int, error: str) -> bool:
        """记录失败，使后续调用能够重新生成。"""
        result = await self._session.execute(
            update(AiChatRunMemory)
            .where(AiChatRunMemory.id == memory_id)
            .values(
                status="failed",
                error_message=error[:2000],
            )
        )
        await self._session.flush()
        return result.rowcount == 1
