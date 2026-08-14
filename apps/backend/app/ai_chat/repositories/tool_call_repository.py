"""使用调用方事务的工具调用持久化。"""

from typing import Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.errors import IdempotencyConflictError, ToolProtocolError
from app.ai_chat.models import AiChatToolCall, utcnow_iso


def json_values_equal(left: Any, right: Any) -> bool:
    """按 JSON 类型和值比较，避免 Python 把 bool 与 int 视为相等。"""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        if left.keys() != right.keys():
            return False
        return all(
            json_values_equal(value, right[key])
            for key, value in left.items()
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            json_values_equal(left_value, right_value)
            for left_value, right_value in zip(left, right, strict=True)
        )
    return left == right


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

    async def materialize(
        self,
        *,
        conversation_id: int,
        run_id: int,
        tool_call_index: int,
        provider_tool_call_id: str | None,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> AiChatToolCall:
        """按 run/index 固化调用，并拒绝同一索引承载不同操作。"""
        statement = (
            sqlite_insert(AiChatToolCall)
            .values(
                conversation_id=conversation_id,
                run_id=run_id,
                tool_call_index=tool_call_index,
                provider_tool_call_id=provider_tool_call_id,
                tool_name=tool_name,
                arguments=arguments,
            )
            .on_conflict_do_nothing()
        )
        await self._session.execute(statement)
        row = await self.get_by_run_index(run_id, tool_call_index)
        provider_row = (
            await self.get_by_run_provider_id(run_id, provider_tool_call_id)
            if provider_tool_call_id is not None
            else None
        )
        if (
            row is None
            or (provider_row is not None and provider_row.id != row.id)
            or row.conversation_id != conversation_id
            or row.tool_name != tool_name
            or not json_values_equal(row.arguments, arguments)
        ):
            raise ToolProtocolError("Tool Call index was reused inconsistently")
        return row

    async def materialize_system(
        self,
        *,
        conversation_id: int,
        run_id: int,
        identity: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> AiChatToolCall:
        """使用 Run 内稳定身份固化服务端持有的调用。"""
        next_index = (
            select(func.coalesce(func.max(AiChatToolCall.tool_call_index), -1) + 1)
            .where(AiChatToolCall.run_id == run_id)
            .scalar_subquery()
        )
        statement = (
            sqlite_insert(AiChatToolCall)
            .values(
                conversation_id=conversation_id,
                run_id=run_id,
                tool_call_index=next_index,
                provider_tool_call_id=identity,
                tool_name=tool_name,
                arguments=arguments,
            )
            .on_conflict_do_nothing()
        )
        await self._session.execute(statement)
        row = await self.get_by_run_provider_id(run_id, identity)
        if (
            row is None
            or row.conversation_id != conversation_id
            or row.tool_name != tool_name
            or not json_values_equal(row.arguments, arguments)
        ):
            raise IdempotencyConflictError(identity)
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

    async def get_by_run_provider_id(
        self, run_id: int, provider_tool_call_id: str
    ) -> AiChatToolCall | None:
        """按 provider 的调用身份查找同一轮记录。"""
        result = await self._session.execute(
            select(AiChatToolCall).where(
                AiChatToolCall.run_id == run_id,
                AiChatToolCall.provider_tool_call_id == provider_tool_call_id,
            )
        )
        return result.scalar_one_or_none()

    async def save_validation(
        self,
        row: AiChatToolCall,
        *,
        proposal_payload: dict[str, Any],
        guard_payload: dict[str, Any],
    ) -> bool:
        """保存 Handler 生成的可信执行依据，但不决定是否审批。"""
        result = await self._session.execute(
            update(AiChatToolCall)
            .where(
                AiChatToolCall.id == row.id,
                AiChatToolCall.status == "received",
            )
            .values(
                proposal_payload=proposal_payload,
                guard_payload=guard_payload,
                status="validated",
                updated_at=utcnow_iso(),
            )
        )
        await self._session.flush()
        return result.rowcount == 1

    async def claim_approval_request(self, tool_call_id: int) -> bool:
        """原子地将已校验调用移入等待审批状态。"""
        result = await self._session.execute(
            update(AiChatToolCall)
            .where(
                AiChatToolCall.id == tool_call_id,
                AiChatToolCall.status == "validated",
            )
            .values(status="awaiting_approval", updated_at=utcnow_iso())
        )
        await self._session.flush()
        return result.rowcount == 1

    async def claim_input_request(self, tool_call_id: int) -> bool:
        """将已校验调用原子转为等待外部输入。"""
        result = await self._session.execute(
            update(AiChatToolCall)
            .where(
                AiChatToolCall.id == tool_call_id,
                AiChatToolCall.status == "validated",
            )
            .values(status="awaiting_input", updated_at=utcnow_iso())
        )
        await self._session.flush()
        return result.rowcount == 1

    async def get_awaiting_input_for_run(self, run_id: int) -> AiChatToolCall | None:
        result = await self._session.execute(
            select(AiChatToolCall).where(
                AiChatToolCall.run_id == run_id,
                AiChatToolCall.status == "awaiting_input",
            )
        )
        rows = list(result.scalars().all())
        if len(rows) > 1:
            raise ToolProtocolError("Run has multiple awaiting-input Tool Calls")
        return rows[0] if rows else None

    async def resolve_input(
        self,
        tool_call_id: int,
        *,
        client_resolution_id: str,
        tool_result: dict[str, Any],
        delivery_status: Literal["pending", "consumed"],
    ) -> bool:
        now = utcnow_iso()
        result = await self._session.execute(
            update(AiChatToolCall)
            .where(
                AiChatToolCall.id == tool_call_id,
                AiChatToolCall.status == "awaiting_input",
                AiChatToolCall.client_resolution_id.is_(None),
            )
            .values(
                status="resolved",
                tool_result=tool_result,
                delivery_status=delivery_status,
                client_resolution_id=client_resolution_id,
                resolved_at=now,
                updated_at=now,
            )
        )
        await self._session.flush()
        return result.rowcount == 1

    async def approve(self, tool_call_id: int, client_resolution_id: str) -> bool:
        """原子地持久化批准决定，执行器只能领取已批准调用。"""
        result = await self._session.execute(
            update(AiChatToolCall)
            .where(
                AiChatToolCall.id == tool_call_id,
                AiChatToolCall.status == "awaiting_approval",
                AiChatToolCall.client_resolution_id.is_(None),
            )
            .values(
                status="approved",
                decision="approve",
                client_resolution_id=client_resolution_id,
                updated_at=utcnow_iso(),
            )
        )
        await self._session.flush()
        return result.rowcount == 1

    async def claim_rejection(
        self, tool_call_id: int, client_resolution_id: str
    ) -> bool:
        """原子地持久化拒绝决定，并领取结果写入权。"""
        result = await self._session.execute(
            update(AiChatToolCall)
            .where(
                AiChatToolCall.id == tool_call_id,
                AiChatToolCall.status == "awaiting_approval",
                AiChatToolCall.client_resolution_id.is_(None),
            )
            .values(
                status="executing",
                decision="reject",
                client_resolution_id=client_resolution_id,
                updated_at=utcnow_iso(),
            )
        )
        await self._session.flush()
        return result.rowcount == 1

    async def resolve(
        self,
        row: AiChatToolCall,
        *,
        decision: str | None,
        tool_result: dict[str, Any],
        client_resolution_id: str | None = None,
        delivery_status: Literal["pending", "consumed"] = "pending",
    ) -> None:
        """持久化不可变的不透明工具结果。"""
        row.status = "resolved"
        row.decision = decision
        row.tool_result = tool_result
        row.delivery_status = delivery_status
        row.client_resolution_id = client_resolution_id
        row.resolved_at = utcnow_iso()
        row.updated_at = row.resolved_at
        await self._session.flush()

    async def resolve_received(
        self,
        tool_call_id: int,
        *,
        tool_result: dict[str, Any],
        delivery_status: Literal["pending", "consumed"] = "pending",
    ) -> bool:
        """原子且仅一次地持久化校验阶段产生的终态结果。"""
        now = utcnow_iso()
        result = await self._session.execute(
            update(AiChatToolCall)
            .where(
                AiChatToolCall.id == tool_call_id,
                AiChatToolCall.status == "received",
            )
            .values(
                status="resolved",
                decision=None,
                tool_result=tool_result,
                delivery_status=delivery_status,
                client_resolution_id=None,
                resolved_at=now,
                updated_at=now,
            )
        )
        await self._session.flush()
        return result.rowcount == 1

    async def claim_execution(
        self,
        tool_call_id: int,
        *,
        from_status: Literal["validated", "approved"],
    ) -> bool:
        """在执行低风险 Tool 前原子认领，事务回滚后仍可重试。"""
        result = await self._session.execute(
            update(AiChatToolCall)
            .where(
                AiChatToolCall.id == tool_call_id,
                AiChatToolCall.status == from_status,
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
