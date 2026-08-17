"""持久化工具调用的原子认领与执行。"""

from dataclasses import dataclass, replace
from typing import Literal, cast

from app.ai_chat.errors import ToolCallNotFoundError, ToolProtocolError
from app.ai_chat.tools.json_validation import ensure_finite_json
from app.ai_chat.tools.registry import ToolRegistry
from app.ai_chat.tools.store import ToolCallStore
from app.ai_chat.tools.operation import ToolExecution
from app.ai_chat.tools.types import ApprovalAction, ToolContext, ToolResult


@dataclass(frozen=True)
class ToolExecutionService:
    """确保一个持久化调用的业务副作用至多被成功提交一次。"""

    store: ToolCallStore
    registry: ToolRegistry

    async def execute(self, context: ToolContext, tool_call_id: int) -> ToolResult:
        """根据数据库事实认领、调用 LangChain Tool 并固化结果。"""
        async with self.store.transaction() as uow:
            row = await uow.calls.get(tool_call_id)
            if row is None:
                raise ToolCallNotFoundError(str(tool_call_id))
            tool = self.registry.get(row.tool_name)
            durable = self.store.call_from_row(row, replayed=True)
            if (
                context.conversation_id != row.conversation_id
                or context.run_id != row.run_id
            ):
                raise ToolProtocolError("Tool Call context identity does not match")
            if durable["status"] == "resolved":
                return self.store.result_from_row(row, replayed=True)
            if durable["status"] not in {"validated", "approved"}:
                raise ToolProtocolError("Tool Call is not ready for execution")

            from_status: Literal["validated", "approved"] = (
                "approved" if durable["status"] == "approved" else "validated"
            )
            prepared_data = dict(row.guard_payload or {})
            decision = cast(ApprovalAction | None, row.decision)
            resolution_id = row.client_resolution_id
            claimed = await uow.calls.claim_execution(
                tool_call_id,
                from_status=from_status,
            )
            if not claimed:
                await uow.session.rollback()
            else:
                try:
                    execution = ToolExecution(
                        context=replace(
                            context,
                            conversation_id=row.conversation_id,
                            run_id=row.run_id,
                            tool_call_id=tool_call_id,
                            session=uow.session,
                        ),
                        prepared_data=prepared_data,
                    )
                    result = await tool.invoke(dict(row.arguments), execution)
                    payload = dict(result.payload)
                    ensure_finite_json(
                        payload,
                        message="Tool execute returned a non-finite result",
                    )
                    await uow.calls.resolve(
                        row,
                        decision=decision,
                        tool_result=payload,
                        client_resolution_id=resolution_id,
                        delivery_status=(
                            "pending" if row.requested_by_model else "consumed"
                        ),
                    )
                    await uow.session.commit()
                    return ToolResult(
                        payload=payload,
                        tool_call_id=tool_call_id,
                        tool_name=row.tool_name,
                        decision=decision,
                        replayed=False,
                    )
                except Exception:
                    await uow.session.rollback()
                    raise
        return await self.store.load_result(tool_call_id, replayed=True)
