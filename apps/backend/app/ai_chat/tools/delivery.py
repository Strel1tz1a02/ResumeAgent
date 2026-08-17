"""工具结果的模型投递状态管理。"""

from dataclasses import dataclass

from app.ai_chat.errors import ToolCallNotFoundError, ToolProtocolError
from app.ai_chat.tools.store import ToolCallStore


@dataclass(frozen=True)
class ToolResultDeliveryService:
    """只在模型成功接收结果后标记消费。"""

    store: ToolCallStore

    async def consume(self, tool_call_id: int) -> None:
        """幂等地将已完成结果从 pending 转为 consumed。"""
        async with self.store.transaction() as uow:
            row = await uow.calls.get(tool_call_id)
            if row is None:
                raise ToolCallNotFoundError(str(tool_call_id))
            if row.status != "resolved":
                raise ToolProtocolError("Tool Call has no result to consume")
            if row.delivery_status == "pending":
                await uow.calls.mark_consumed([row])
                await uow.session.commit()
