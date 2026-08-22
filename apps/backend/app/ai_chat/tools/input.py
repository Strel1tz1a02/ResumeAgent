"""工具外部输入请求、解决与幂等重放。"""

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from app.ai_chat.errors import (
    IdempotencyConflictError,
    ToolCallNotFoundError,
    ToolProtocolError,
)
from app.ai_chat.tools.json_validation import ensure_finite_json
from app.ai_chat.tools.registry import ToolRegistry
from app.ai_chat.repositories.tool_repository import json_values_equal
from app.ai_chat.tools.store import ToolCallStore
from app.ai_chat.tools.types import ToolCall, ToolResult
from app.ai_chat.types import JsonObject


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
            call = self.store.call_from_row(row, replayed=True)
            if call["status"] in {"awaiting_input", "resolved"}:
                return call
            if call["status"] != "validated":
                raise ToolProtocolError("Tool Call cannot request input")
            claimed = await uow.calls.claim_input_request(tool_call_id)
            if claimed:
                await uow.session.commit()
            else:
                await uow.session.rollback()
        return await self.store.load_call(tool_call_id, replayed=not claimed)

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
                return self.store.result_from_row(row, replayed=True)
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
                    await uow.session.commit()
                else:
                    await uow.session.rollback()
            except IntegrityError:
                await uow.session.rollback()
                claimed = False
        if claimed:
            return await self.store.load_result(tool_call_id, replayed=False)
        resolved = await self.store.load_result(tool_call_id, replayed=True)
        async with self.store.transaction() as uow:
            row = await uow.calls.get(tool_call_id)
            if (
                row is None
                or row.client_resolution_id != client_resolution_id
                or not json_values_equal(row.tool_result, payload)
            ):
                raise IdempotencyConflictError(client_resolution_id)
        return resolved
