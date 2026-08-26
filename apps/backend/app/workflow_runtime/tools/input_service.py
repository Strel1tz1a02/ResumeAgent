"""工具外部输入请求、解决与幂等重放。"""

from dataclasses import dataclass

from app.workflow_runtime.errors import (
    IdempotencyConflictError,
    ToolCallNotFoundError,
    ToolPersistenceConflictError,
    ToolProtocolError,
)
from app.workflow_runtime.tools.json_validation import (
    ensure_finite_json,
    json_values_equal,
)
from app.workflow_runtime.tools.persistence import (
    ToolCallStore,
    load_tool_result,
    tool_call_from_record,
    tool_result_from_record,
)
from app.workflow_runtime.tools.registry import ToolRegistry
from app.workflow_runtime.tools.types import ToolCall, ToolResult
from app.workflow_runtime.types import JsonObject


@dataclass(frozen=True)
class ToolInputService:
    """管理不执行副作用、等待外部输入解决的工具调用。"""

    store: ToolCallStore
    registry: ToolRegistry

    async def request(self, tool_call_id: int) -> ToolCall:
        """将已准备调用原子转为等待外部输入。"""
        async with self.store.transaction() as uow:
            row = await uow.calls.get(tool_call_id)
            if row is None:
                raise ToolCallNotFoundError(str(tool_call_id))
            self.registry.get(row.tool_name)
            call = tool_call_from_record(row, replayed=True)
            if call["status"] in {"awaiting_input", "resolved"}:
                return call
            if call["status"] != "validated":
                raise ToolProtocolError("Tool Call cannot request input")
            claimed = await uow.calls.claim_input_request(tool_call_id)
            if claimed:
                await uow.commit()
            else:
                await uow.rollback()
        from app.workflow_runtime.tools.persistence import load_tool_call

        return await load_tool_call(self.store, tool_call_id, replayed=not claimed)

    async def resolve(
        self,
        tool_call_id: int,
        client_resolution_id: str,
        payload: JsonObject,
    ) -> ToolResult:
        """原子固化外部输入结果，并校验重复请求是否一致。"""
        if not client_resolution_id.strip():
            raise ToolProtocolError("Input resolution id must be non-empty")
        ensure_finite_json(payload, message="Tool input contains a non-finite number")
        async with self.store.transaction() as uow:
            row = await uow.calls.get(tool_call_id)
            if row is None:
                raise ToolCallNotFoundError(str(tool_call_id))
            self.registry.get(row.tool_name)
            if row.status == "resolved":
                if (
                    row.client_resolution_id != client_resolution_id
                    or not json_values_equal(row.tool_result, payload)
                ):
                    raise IdempotencyConflictError(client_resolution_id)
                return tool_result_from_record(row, replayed=True)
            if row.status != "awaiting_input":
                raise ToolProtocolError("Tool Call is not awaiting input")
            try:
                claimed = await uow.calls.resolve_input(
                    tool_call_id,
                    client_resolution_id=client_resolution_id,
                    tool_result=dict(payload),
                    delivery_status=(
                        "pending" if row.requested_by_model else "consumed"
                    ),
                )
                if claimed:
                    await uow.commit()
                else:
                    await uow.rollback()
            except ToolPersistenceConflictError:
                await uow.rollback()
                claimed = False
        if claimed:
            return await load_tool_result(self.store, tool_call_id, replayed=False)
        resolved = await load_tool_result(self.store, tool_call_id, replayed=True)
        async with self.store.transaction() as uow:
            row = await uow.calls.get(tool_call_id)
            if (
                row is None
                or row.client_resolution_id != client_resolution_id
                or not json_values_equal(row.tool_result, payload)
            ):
                raise IdempotencyConflictError(client_resolution_id)
        return resolved
