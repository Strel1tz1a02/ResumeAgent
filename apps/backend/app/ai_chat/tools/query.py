"""持久化工具调用的查询接口。"""

from dataclasses import dataclass

from app.ai_chat.errors import ToolCallNotFoundError
from app.ai_chat.tools.registry import ToolRegistry
from app.ai_chat.tools.store import ToolCallStore
from app.ai_chat.tools.types import ToolCall


@dataclass(frozen=True)
class ToolCallQueryService:
    """按持久化身份查询调用，并验证工具注册关系。"""

    store: ToolCallStore
    registry: ToolRegistry

    async def get(self, tool_call_id: int) -> ToolCall:
        """按数据库主键加载调用。"""
        call = await self.store.load_call(tool_call_id, replayed=True)
        self.registry.get(call["name"])
        return call

    async def get_by_run_identity(self, run_id: int, identity: str) -> ToolCall:
        """按 Run 内稳定身份加载调用。"""
        async with self.store.transaction() as uow:
            row = await uow.calls.get_by_run_provider_id(run_id, identity)
            if row is None:
                raise ToolCallNotFoundError(identity)
            self.registry.get(row.tool_name)
            return self.store.call_from_row(row, replayed=True)
