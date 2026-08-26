"""持久化工具调用的查询接口。"""

from dataclasses import dataclass

from app.workflow_runtime.tools.persistence import ToolCallStore, load_tool_call
from app.workflow_runtime.tools.registry import ToolRegistry
from app.workflow_runtime.tools.types import ToolCall


@dataclass(frozen=True)
class ToolCallQueryService:
    """按持久化身份查询调用，并验证工具注册关系。"""

    store: ToolCallStore
    registry: ToolRegistry

    async def get(self, tool_call_id: int) -> ToolCall:
        """按数据库主键加载调用。"""
        call = await load_tool_call(self.store, tool_call_id, replayed=True)
        self.registry.get(call["name"])
        return call
