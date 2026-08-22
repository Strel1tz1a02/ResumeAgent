"""工具风险路由与审批生命周期。"""

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from app.ai_chat.errors import (
    IdempotencyConflictError,
    InteractionStateError,
    ToolCallNotFoundError,
    ToolProtocolError,
)
from app.ai_chat.tools.approval.policy import ApprovalRoute, ToolApprovalPolicy, ToolRisk
from app.ai_chat.tools.registry import ToolRegistry
from app.ai_chat.tools.store import ToolCallStore
from app.ai_chat.tools.types import ApprovalDecision, ToolCall
from app.ai_chat.types import JsonObject


@dataclass(frozen=True)
class ToolApprovalService:
    """执行风险路由、审批申请和审批决定的持久化生命周期。"""

    store: ToolCallStore
    registry: ToolRegistry
    policy: ToolApprovalPolicy

    def risk(self, tool_name: str) -> ToolRisk:
        """读取工具风险策略。"""
        return self.policy.risk(tool_name)

    def route(self, call: ToolCall) -> ApprovalRoute:
        """根据完整调用决定直接执行或进入审批。"""
        return self.policy.route(call)

    def proposal(self, tool_name: str, prepared_data: JsonObject) -> JsonObject:
        """生成审批界面允许展示的数据。"""
        return self.policy.proposal(tool_name, prepared_data)

    async def request(self, tool_call_id: int) -> ToolCall:
        """持久化审批申请，不替审批策略作出决定。"""
        async with self.store.transaction() as uow:
            row = await uow.calls.get(tool_call_id)
            if row is None:
                raise ToolCallNotFoundError(str(tool_call_id))
            self.registry.get(row.tool_name)
            call = self.store.call_from_row(row, replayed=True)
            if call["status"] in {"awaiting_approval", "approved", "resolved"}:
                return call
            if call["status"] != "validated":
                raise ToolProtocolError("Tool Call cannot request approval")
            claimed = await uow.calls.claim_approval_request(tool_call_id)
            if claimed:
                await uow.session.commit()
            else:
                await uow.session.rollback()
        durable = await self.store.load_call(tool_call_id, replayed=not claimed)
        if durable["status"] not in {"awaiting_approval", "approved", "resolved"}:
            raise ToolProtocolError("Tool Call approval request was not persisted")
        return durable

    async def _reload_decision(self, approval: ApprovalDecision) -> ToolCall:
        """状态竞争失败后，根据最新持久化快照映射审批结果。"""
        tool_call_id = approval["tool_call_id"]
        resolution_id = approval["client_resolution_id"]
        async with self.store.transaction() as uow:
            row = await uow.calls.get(tool_call_id)
            if row is None:
                raise ToolCallNotFoundError(str(tool_call_id))
            owner = await uow.calls.get_by_resolution_id(
                row.conversation_id,
                resolution_id,
            )
            if owner is not None and owner.id != tool_call_id:
                raise IdempotencyConflictError(resolution_id)
            self.registry.get(row.tool_name)
            call = self.store.call_from_row(row, replayed=True)
            if call["status"] not in {"approved", "resolved"}:
                raise InteractionStateError(str(tool_call_id))
            if (
                row.decision != approval["decision"]
                or row.client_resolution_id != resolution_id
            ):
                raise IdempotencyConflictError(resolution_id)
            return call

    async def record_decision(self, approval: ApprovalDecision) -> ToolCall:
        """持久化外部审批决定，并处理并发幂等竞争。"""
        tool_call_id = approval["tool_call_id"]
        decision = approval["decision"]
        resolution_id = approval["client_resolution_id"]
        if decision not in ("approve", "reject"):
            raise ToolProtocolError("Unsupported approval decision")
        if not isinstance(resolution_id, str) or not resolution_id.strip():
            raise ToolProtocolError("Approval resolution id must be non-empty")
        async with self.store.transaction() as uow:
            row = await uow.calls.get(tool_call_id)
            if row is None:
                raise ToolCallNotFoundError(str(tool_call_id))
            self.registry.get(row.tool_name)
            call = self.store.call_from_row(row, replayed=True)
            if call["status"] in {"approved", "resolved"}:
                if (
                    row.decision != decision
                    or row.client_resolution_id != resolution_id
                ):
                    raise IdempotencyConflictError(resolution_id)
                return call
            if call["status"] != "awaiting_approval":
                raise InteractionStateError(str(tool_call_id))
            owner = await uow.calls.get_by_resolution_id(
                row.conversation_id,
                resolution_id,
            )
            if owner is not None and owner.id != tool_call_id:
                raise IdempotencyConflictError(resolution_id)
            try:
                if decision == "approve":
                    claimed = await uow.calls.approve(tool_call_id, resolution_id)
                else:
                    claimed = await uow.calls.claim_rejection(
                        tool_call_id,
                        resolution_id,
                    )
                    if claimed:
                        await uow.calls.resolve(
                            row,
                            decision="reject",
                            tool_result={"outcome": "rejected"},
                            client_resolution_id=resolution_id,
                            delivery_status=(
                                "pending" if row.requested_by_model else "consumed"
                            ),
                        )
                if claimed:
                    await uow.session.commit()
                else:
                    await uow.session.rollback()
            except IntegrityError:
                await uow.session.rollback()
                claimed = False
        if claimed:
            return await self.store.load_call(tool_call_id, replayed=False)
        return await self._reload_decision(approval)
