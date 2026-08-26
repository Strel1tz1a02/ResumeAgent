"""工具生命周期与具体存储后端之间的持久化端口。"""

from __future__ import annotations

from collections.abc import Collection
from contextlib import AbstractAsyncContextManager
from typing import Literal, Protocol, cast

from app.workflow_runtime.errors import ToolProtocolError
from app.workflow_runtime.tools.types import (
    ApprovalAction,
    ToolCall,
    ToolCallStatus,
    ToolResources,
    ToolResult,
)
from app.workflow_runtime.types import JsonObject

DeliveryStatus = Literal["pending", "consumed"]
ExecutableStatus = Literal["validated", "approved"]


class ToolCallRecord(Protocol):
    """Runtime 可读取的存储记录；实现可以是 ORM、文档或内存对象。"""

    id: int
    thread_id: int | str
    run_id: int | str
    tool_call_index: int
    provider_tool_call_id: str | None
    requested_by_model: bool
    tool_name: str
    arguments: JsonObject
    interaction_payload: JsonObject | None
    guard_payload: JsonObject | None
    status: str
    decision: str | None
    tool_result: JsonObject | None
    delivery_status: str | None
    client_resolution_id: str | None
    resolved_at: str | None


class ToolCallPersistence(Protocol):
    """单个事务内的 Tool Call CAS 操作。"""

    async def materialize(
        self,
        *,
        thread_id: int | str,
        run_id: int | str,
        tool_call_index: int,
        provider_tool_call_id: str | None,
        tool_name: str,
        arguments: JsonObject,
    ) -> ToolCallRecord: ...

    async def materialize_system(
        self,
        *,
        thread_id: int | str,
        run_id: int | str,
        identity: str,
        tool_name: str,
        arguments: JsonObject,
        requested_by_model: bool = False,
    ) -> ToolCallRecord: ...

    async def get(self, tool_call_id: int) -> ToolCallRecord | None: ...

    async def get_by_resolution_id(
        self,
        thread_id: int | str,
        client_resolution_id: str,
    ) -> ToolCallRecord | None: ...

    async def save_validation(
        self,
        row: ToolCallRecord,
        *,
        interaction_payload: JsonObject,
        guard_payload: JsonObject,
    ) -> bool: ...

    async def claim_approval_request(self, tool_call_id: int) -> bool: ...
    async def claim_input_request(self, tool_call_id: int) -> bool: ...

    async def resolve_input(
        self,
        tool_call_id: int,
        *,
        client_resolution_id: str,
        tool_result: JsonObject,
        delivery_status: DeliveryStatus,
    ) -> bool: ...

    async def approve(self, tool_call_id: int, client_resolution_id: str) -> bool: ...

    async def claim_rejection(
        self,
        tool_call_id: int,
        client_resolution_id: str,
    ) -> bool: ...

    async def resolve(
        self,
        row: ToolCallRecord,
        *,
        decision: str | None,
        tool_result: JsonObject,
        client_resolution_id: str | None = None,
        delivery_status: DeliveryStatus = "pending",
    ) -> None: ...

    async def resolve_received(
        self,
        tool_call_id: int,
        *,
        tool_result: JsonObject,
        delivery_status: DeliveryStatus = "pending",
    ) -> bool: ...

    async def claim_execution(
        self,
        tool_call_id: int,
        *,
        from_status: ExecutableStatus,
    ) -> bool: ...

    async def pending_results(
        self,
        thread_id: int | str,
    ) -> list[ToolCallRecord]: ...

    async def mark_consumed(self, rows: Collection[ToolCallRecord]) -> None: ...


class ToolCallUnitOfWork(Protocol):
    calls: ToolCallPersistence
    resources: ToolResources

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class ToolCallStore(Protocol):
    """可由 SQLite、内存或远端服务实现的工具持久化端口。"""

    def transaction(
        self,
    ) -> AbstractAsyncContextManager[ToolCallUnitOfWork]: ...

    async def pending_results(self, thread_id: int | str) -> list[ToolCall]: ...

    async def consume_results(
        self,
        tool_call_ids: Collection[int],
        *,
        resources: ToolResources | None = None,
    ) -> None: ...


def validate_tool_call_record(row: ToolCallRecord) -> None:
    """拒绝状态与载荷相互矛盾的持久化记录。"""
    if not isinstance(row.requested_by_model, bool):
        raise ToolProtocolError("Tool Call has no valid model-origin marker")
    if row.status == "received" and any(
        value is not None
        for value in (
            row.interaction_payload,
            row.guard_payload,
            row.decision,
            row.client_resolution_id,
            row.tool_result,
            row.delivery_status,
            row.resolved_at,
        )
    ):
        raise ToolProtocolError("Received Tool Call has unexpected state data")
    if row.status in {
        "validated",
        "awaiting_approval",
        "awaiting_input",
        "approved",
    } and (row.interaction_payload is None or row.guard_payload is None):
        raise ToolProtocolError("Tool Call has no trusted payload")
    if row.status in {"validated", "awaiting_approval", "awaiting_input"} and (
        row.tool_result is not None
        or row.decision is not None
        or row.client_resolution_id is not None
    ):
        raise ToolProtocolError("Tool Call has unexpected resolution data")
    if row.status == "approved":
        if row.tool_result is not None:
            raise ToolProtocolError("Approved Tool Call already has a result")
        if (
            row.decision != "approve"
            or not row.client_resolution_id
            or not row.client_resolution_id.strip()
        ):
            raise ToolProtocolError("Approved Tool Call has no approval identity")
    if row.status == "resolved":
        if row.tool_result is None:
            raise ToolProtocolError("Resolved Tool Call has no result")
        if row.decision not in (None, "approve", "reject"):
            raise ToolProtocolError("Resolved Tool Call has an unsupported decision")
        if row.decision is not None:
            if row.interaction_payload is None or row.guard_payload is None:
                raise ToolProtocolError(
                    "Resolved approval Tool Call has incomplete trusted state"
                )
            if (
                not row.client_resolution_id
                or not row.client_resolution_id.strip()
            ):
                raise ToolProtocolError("Resolved Tool Call has no resolution identity")
        elif (row.interaction_payload is None) != (row.guard_payload is None):
            raise ToolProtocolError("Resolved Tool Call has incomplete trusted state")
        if row.delivery_status not in {"pending", "consumed"}:
            raise ToolProtocolError("Resolved Tool Call has no delivery state")
        if not row.requested_by_model and row.delivery_status != "consumed":
            raise ToolProtocolError("System Tool Call cannot have a pending model delivery")
        if not row.resolved_at or not row.resolved_at.strip():
            raise ToolProtocolError("Resolved Tool Call has no resolved timestamp")
    elif row.delivery_status is not None or row.resolved_at is not None:
        raise ToolProtocolError("Unresolved Tool Call has terminal delivery data")


def tool_call_from_record(row: ToolCallRecord, *, replayed: bool) -> ToolCall:
    if row.status not in {
        "validated",
        "awaiting_approval",
        "awaiting_input",
        "approved",
        "resolved",
    }:
        raise ToolProtocolError(f"Unsupported Tool Call status: {row.status}")
    validate_tool_call_record(row)
    status = cast(ToolCallStatus, row.status)
    should_execute: bool | None = None
    if status == "approved":
        should_execute = True
    elif status == "resolved":
        should_execute = False
    return {
        "tool_call_id": row.id,
        "index": row.tool_call_index,
        "provider_id": row.provider_tool_call_id,
        "requested_by_model": row.requested_by_model,
        "name": row.tool_name,
        "arguments": dict(row.arguments),
        "status": status,
        "interaction_payload": (
            dict(row.interaction_payload)
            if row.interaction_payload is not None
            else None
        ),
        "should_execute": should_execute,
        "result": dict(row.tool_result) if row.tool_result is not None else None,
        "replayed": replayed,
    }


def tool_result_from_record(row: ToolCallRecord, *, replayed: bool) -> ToolResult:
    validate_tool_call_record(row)
    if row.status != "resolved" or row.tool_result is None:
        raise ToolProtocolError("Tool Call has no durable result")
    return ToolResult(
        payload=dict(row.tool_result),
        tool_call_id=row.id,
        tool_name=row.tool_name,
        decision=cast(ApprovalAction | None, row.decision),
        replayed=replayed,
    )


async def load_tool_call(
    store: ToolCallStore,
    tool_call_id: int,
    *,
    replayed: bool,
) -> ToolCall:
    async with store.transaction() as uow:
        row = await uow.calls.get(tool_call_id)
        if row is None:
            raise ToolProtocolError("Persisted Tool Call disappeared")
        return tool_call_from_record(row, replayed=replayed)


async def load_tool_result(
    store: ToolCallStore,
    tool_call_id: int,
    *,
    replayed: bool,
) -> ToolResult:
    async with store.transaction() as uow:
        row = await uow.calls.get(tool_call_id)
        if row is None:
            raise ToolProtocolError("Persisted Tool Call disappeared")
        return tool_result_from_record(row, replayed=replayed)
