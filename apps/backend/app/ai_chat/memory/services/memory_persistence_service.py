"""记忆模块内部的数据库访问边界。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app import database as database_module
from app.ai_chat.memory.runs import (
    Memory,
    OriginRun,
    Run,
)
from app.ai_chat.models import AiChatRunMemory
from app.ai_chat.repositories.memory_repository import MemoryRepository
from app.ai_chat.repositories.origin_run_repository import OriginRunRepository


@dataclass(frozen=True)
class CompressionSlot:
    """待压缩记录的数据库占位及可能已有的结果。"""

    memory_id: int
    completed: Memory | None


def _memory(row: AiChatRunMemory) -> Memory:
    """把数据库记录转换为领域 Memory。"""
    return Memory(
        run_id=row.run_id,
        token_count=row.memory_token_count,
        **dict(row.core),
        other=dict(row.other),
    )


class MemoryPersistenceService:
    """集中读取历史并持久化每个 Run 的累计记忆结果。"""

    @staticmethod
    async def load_history(run_id: int) -> list[Run]:
        """批量读取目标 Run 之前的完整历史。"""
        async with database_module.db.session() as session:
            origin_runs = await OriginRunRepository(session).history_before(run_id)
            return await MemoryPersistenceService._load_runs(session, origin_runs)

    @staticmethod
    async def load_history_through(run_id: int) -> list[Run]:
        """批量读取截至目标终态 Run 的完整历史。"""
        async with database_module.db.session() as session:
            origin_runs = await OriginRunRepository(session).history_through(run_id)
            return await MemoryPersistenceService._load_runs(session, origin_runs)

    @staticmethod
    async def _load_runs(
        session: AsyncSession,
        origin_runs: list[OriginRun],
    ) -> list[Run]:
        """把原始 Run 与已完成或已跳过的有效快照合并。"""
        repository = MemoryRepository(session)
        rows = await repository.get_by_run_ids(
            [origin_run.run_id for origin_run in origin_runs]
        )
        runs = []
        for origin_run in origin_runs:
            row = rows.get(origin_run.run_id)
            memory = (
                _memory(row)
                if row is not None and row.status in {"completed", "skipped"}
                else None
            )
            runs.append(Run(origin=origin_run, memory=memory))
        return runs

    @staticmethod
    async def prepare_compression(
        *,
        origin_run: OriginRun,
    ) -> CompressionSlot:
        """取得当前 Run 的压缩占位。"""
        async with database_module.db.session() as session:
            repository = MemoryRepository(session)
            row = await repository.get_by_run_id(origin_run.run_id)
            if row is None:
                row = await repository.create_placeholder(
                    run_id=origin_run.run_id,
                )
                await session.commit()
            return CompressionSlot(
                memory_id=row.id,
                completed=(
                    _memory(row)
                    if row.status in {"completed", "skipped"}
                    else None
                ),
            )

    @staticmethod
    async def complete(
        *,
        memory_id: int,
        memory: Memory,
    ) -> Memory | None:
        """持久化并返回已完成的累计记忆。"""
        async with database_module.db.session() as session:
            repository = MemoryRepository(session)
            completed = await repository.complete(
                memory_id=memory_id,
                memory=memory,
            )
            if not completed:
                row = await repository.get(memory_id)
                if row is None or row.status not in {"completed", "skipped"}:
                    return None
                return _memory(row)
            await session.commit()
            return memory

    @staticmethod
    async def skip(
        *,
        memory_id: int,
        memory: Memory,
        error: str,
    ) -> Memory | None:
        """持久化无变化的 skipped 快照。"""
        async with database_module.db.session() as session:
            repository = MemoryRepository(session)
            skipped = await repository.skip(
                memory_id=memory_id,
                memory=memory,
                error=error,
            )
            if not skipped:
                row = await repository.get(memory_id)
                if row is None or row.status not in {"completed", "skipped"}:
                    return None
                return _memory(row)
            await session.commit()
            return memory

    @staticmethod
    async def wait_until_ready(
        run_id: int,
        *,
        timeout_seconds: float,
        poll_seconds: float,
    ) -> bool:
        """等待数据库中出现可用于链路的 completed/skipped Snapshot。"""
        deadline = time.monotonic() + timeout_seconds
        while True:
            async with database_module.db.session() as session:
                row = await MemoryRepository(session).get_by_run_id(run_id)
                if row is not None and row.status in {"completed", "skipped"}:
                    return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(poll_seconds, remaining))
