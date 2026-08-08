"""使用调用方事务的工具调用持久化。"""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.models import AiChatToolCall, utcnow_iso


class ToolCallRepository:
    """持久化不透明工具载荷和通用投递状态。"""

    def __init__(self, session: AsyncSession) -> None:
        """将仓储操作绑定到调用方持有的会话。"""
        self._session = session

    async def create(
        self,
        *,
        conversation_id: int,
        run_id: int,
        tool_call_index: int,
        provider_tool_call_id: str | None,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> AiChatToolCall:
        """持久化一个已完整组装的工具调用。"""
        row = AiChatToolCall(
            conversation_id=conversation_id,
            run_id=run_id,
            tool_call_index=tool_call_index,
            provider_tool_call_id=provider_tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, tool_call_id: int) -> AiChatToolCall | None:
        """根据 ID 返回工具调用。"""
        return await self._session.get(AiChatToolCall, tool_call_id)

    async def get_by_resolution_id(
        self, conversation_id: int, client_resolution_id: str
    ) -> AiChatToolCall | None:
        """查找幂等的提案审批记录。"""
        result = await self._session.execute(
            select(AiChatToolCall).where(
                AiChatToolCall.conversation_id == conversation_id,
                AiChatToolCall.client_resolution_id == client_resolution_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_run_index(
        self, run_id: int, tool_call_index: int
    ) -> AiChatToolCall | None:
        """使用本轮稳定索引查找重放的工具调用。"""
        result = await self._session.execute(
            select(AiChatToolCall).where(
                AiChatToolCall.run_id == run_id,
                AiChatToolCall.tool_call_index == tool_call_index,
            )
        )
        return result.scalar_one_or_none()

    async def request_approval(
        self,
        row: AiChatToolCall,
        *,
        proposal_payload: dict[str, Any],
        guard_payload: dict[str, Any],
    ) -> None:
        """将工具调用标记为等待用户决定。"""
        row.proposal_payload = proposal_payload
        row.guard_payload = guard_payload
        row.status = "awaiting_approval"
        row.updated_at = utcnow_iso()
        await self._session.flush()

    async def resolve(
        self,
        row: AiChatToolCall,
        *,
        decision: str | None,
        tool_result: dict[str, Any],
        client_resolution_id: str | None = None,
    ) -> None:
        """持久化不可变的不透明工具结果。"""
        row.status = "resolved"
        row.decision = decision
        row.tool_result = tool_result
        row.delivery_status = "pending"
        row.client_resolution_id = client_resolution_id
        row.resolved_at = utcnow_iso()
        row.updated_at = row.resolved_at
        await self._session.flush()

    async def claim_resolution(
        self,
        tool_call_id: int,
        *,
        decision: str,
        client_resolution_id: str,
    ) -> bool:
        """原子认领待审批 Tool Call，防止跨进程重复执行业务副作用。"""
        result = await self._session.execute(
            update(AiChatToolCall)
            .where(
                AiChatToolCall.id == tool_call_id,
                AiChatToolCall.status == "awaiting_approval",
                AiChatToolCall.client_resolution_id.is_(None),
            )
            .values(
                decision=decision,
                client_resolution_id=client_resolution_id,
                updated_at=utcnow_iso(),
            )
        )
        await self._session.flush()
        return result.rowcount == 1

    async def claim_execution(self, tool_call_id: int) -> bool:
        """在执行低风险 Tool 前原子认领，事务回滚后仍可重试。"""
        result = await self._session.execute(
            update(AiChatToolCall)
            .where(
                AiChatToolCall.id == tool_call_id,
                AiChatToolCall.status == "received",
            )
            .values(
                status="executing",
                updated_at=utcnow_iso(),
            )
        )
        await self._session.flush()
        return result.rowcount == 1

    async def pending_results(self, conversation_id: int) -> list[AiChatToolCall]:
        """返回尚未被成功模型响应消费的工具结果。"""
        result = await self._session.execute(
            select(AiChatToolCall)
            .where(
                AiChatToolCall.conversation_id == conversation_id,
                AiChatToolCall.status == "resolved",
                AiChatToolCall.delivery_status == "pending",
            )
            .order_by(AiChatToolCall.id)
        )
        return list(result.scalars().all())

    async def mark_consumed(self, rows: list[AiChatToolCall]) -> None:
        """在模型完整响应后将工具结果标记为已消费。"""
        now = utcnow_iso()
        for row in rows:
            row.delivery_status = "consumed"
            row.updated_at = now
        await self._session.flush()
