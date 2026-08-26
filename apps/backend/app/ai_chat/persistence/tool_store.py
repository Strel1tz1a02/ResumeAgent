"""Conversation 使用的 SQLAlchemy Tool Call 持久化后端。"""

from collections.abc import AsyncIterator, Callable, Collection
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.persistence.tool_repository import ToolCallRepository
from app.workflow_runtime.errors import ToolPersistenceConflictError
from app.workflow_runtime.tools import (
    ToolCall,
    ToolResources,
)
from app.workflow_runtime.tools.persistence import (
    ToolCallPersistence,
    tool_call_from_record,
)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
ToolCallRepositoryFactory = Callable[[AsyncSession], ToolCallPersistence]


@dataclass(frozen=True)
class ToolCallUnitOfWork:
    """绑定同一 SQLAlchemy 事务的工具调用工作单元。"""

    session: AsyncSession
    calls: ToolCallPersistence

    @property
    def resources(self) -> ToolResources:
        return ToolResources((self.session,))

    async def commit(self) -> None:
        try:
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise ToolPersistenceConflictError(str(error)) from error

    async def rollback(self) -> None:
        await self.session.rollback()


@dataclass(frozen=True)
class SqlAlchemyToolCallStore:
    """用 Conversation 数据库实现 Runtime 的 ToolCallStore 端口。"""

    session_factory: SessionFactory
    repository_factory: ToolCallRepositoryFactory = ToolCallRepository

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[ToolCallUnitOfWork]:
        async with self.session_factory() as session:
            yield ToolCallUnitOfWork(
                session=session,
                calls=self.repository_factory(session),
            )

    async def pending_results(self, thread_id: int | str) -> list[ToolCall]:
        if not isinstance(thread_id, int):
            return []
        async with self.transaction() as uow:
            rows = await uow.calls.pending_results(thread_id)
            return [tool_call_from_record(row, replayed=True) for row in rows]

    async def consume_results(
        self,
        tool_call_ids: Collection[int],
        *,
        resources: ToolResources | None = None,
    ) -> None:
        session = resources.get(AsyncSession) if resources is not None else None
        if session is not None:
            repository = self.repository_factory(session)
            rows = [
                row
                for tool_call_id in set(tool_call_ids)
                if (row := await repository.get(tool_call_id)) is not None
                and row.delivery_status == "pending"
            ]
            await repository.mark_consumed(rows)
            return
        async with self.transaction() as uow:
            rows = [
                row
                for tool_call_id in set(tool_call_ids)
                if (row := await uow.calls.get(tool_call_id)) is not None
                and row.delivery_status == "pending"
            ]
            if rows:
                await uow.calls.mark_consumed(rows)
                await uow.commit()
