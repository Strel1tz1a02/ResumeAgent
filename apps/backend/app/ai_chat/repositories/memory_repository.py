"""使用调用方事务维护按 Run 占位的会话记忆。"""

from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.memory.operations import MemoryDocument, MemoryOperation
from app.ai_chat.memory.run_bundles import RunBundle
from app.ai_chat.models import AiChatRunMemory, utcnow_iso


class MemoryRepository:
    """维护压缩占位与累计记忆结果，不负责执行控制。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_run_id(self, run_id: int) -> AiChatRunMemory | None:
        result = await self._session.execute(
            select(AiChatRunMemory).where(AiChatRunMemory.run_id == run_id)
        )
        return result.scalar_one_or_none()

    async def create_placeholder(
        self,
        *,
        conversation_id: int,
        parent_memory_id: int | None,
        bundle: RunBundle,
    ) -> AiChatRunMemory:
        """在 LLM 调用前幂等写入 pending 占位。"""
        statement = (
            sqlite_insert(AiChatRunMemory)
            .values(
                conversation_id=conversation_id,
                run_id=bundle.run_id,
                parent_memory_id=parent_memory_id,
                source_bundle_hash=bundle.stable_hash(),
                status="pending",
                operations=[],
                core={},
                other={},
                memory_token_count=0,
            )
            .on_conflict_do_nothing(index_elements=["run_id"])
        )
        await self._session.execute(statement)
        await self._session.flush()
        row = await self.get_by_run_id(bundle.run_id)
        if row is None:
            raise RuntimeError("memory placeholder was not persisted")
        return row

    async def complete(
        self,
        *,
        memory_id: int,
        operations: list[MemoryOperation],
        document: MemoryDocument,
        memory_token_count: int,
    ) -> bool:
        """写入完整校验后的记忆结果。"""
        now = utcnow_iso()
        result = await self._session.execute(
            update(AiChatRunMemory)
            .where(AiChatRunMemory.id == memory_id)
            .values(
                status="completed",
                operations=[item.model_dump(mode="json") for item in operations],
                core=document.core_json(),
                other=document.other,
                memory_token_count=memory_token_count,
                error_message=None,
                completed_at=now,
                updated_at=now,
            )
        )
        await self._session.flush()
        return result.rowcount == 1

    async def fail(self, *, memory_id: int, error: str) -> bool:
        """记录失败，使后续调用能够重新生成。"""
        now = utcnow_iso()
        result = await self._session.execute(
            update(AiChatRunMemory)
            .where(AiChatRunMemory.id == memory_id)
            .values(
                status="failed",
                error_message=error[:2000],
                updated_at=now,
            )
        )
        await self._session.flush()
        return result.rowcount == 1

    async def delete_chain_from(self, memory_id: int) -> None:
        """源 Run 或父链变化时删除该节点；数据库级联删除后继。"""
        await self._session.execute(
            delete(AiChatRunMemory).where(AiChatRunMemory.id == memory_id)
        )
        await self._session.flush()
