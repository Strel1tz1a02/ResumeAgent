"""不依赖 Conversation 或数据库的进程内 Tool Call Store。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.workflow_runtime.errors import (
    IdempotencyConflictError,
    ToolProtocolError,
)
from app.workflow_runtime.tools.json_validation import json_values_equal
from app.workflow_runtime.tools.persistence import (
    DeliveryStatus,
    ExecutableStatus,
    ToolCallRecord,
    tool_call_from_record,
)
from app.workflow_runtime.tools.types import ToolCall, ToolResources
from app.workflow_runtime.types import JsonObject


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class _MemoryToolCall:
    id: int
    thread_id: int | str
    run_id: int | str
    tool_call_index: int
    provider_tool_call_id: str | None
    requested_by_model: bool
    tool_name: str
    arguments: JsonObject
    interaction_payload: JsonObject | None = None
    guard_payload: JsonObject | None = None
    status: str = "received"
    decision: str | None = None
    tool_result: JsonObject | None = None
    delivery_status: str | None = None
    client_resolution_id: str | None = None
    resolved_at: str | None = None
    updated_at: str = field(default_factory=_now)


@dataclass
class _MemoryState:
    rows: dict[int, _MemoryToolCall] = field(default_factory=dict)
    next_id: int = 1


class _MemoryToolCallPersistence:
    def __init__(self, state: _MemoryState) -> None:
        self._state = state

    def _by_run_index(
        self,
        run_id: int | str,
        index: int,
    ) -> _MemoryToolCall | None:
        return next(
            (
                row
                for row in self._state.rows.values()
                if row.run_id == run_id and row.tool_call_index == index
            ),
            None,
        )

    def _by_run_provider(
        self,
        run_id: int | str,
        provider_id: str,
    ) -> _MemoryToolCall | None:
        return next(
            (
                row
                for row in self._state.rows.values()
                if row.run_id == run_id
                and row.provider_tool_call_id == provider_id
            ),
            None,
        )

    def _insert(
        self,
        *,
        thread_id: int | str,
        run_id: int | str,
        index: int,
        provider_id: str | None,
        tool_name: str,
        arguments: JsonObject,
        requested_by_model: bool,
    ) -> _MemoryToolCall:
        row = _MemoryToolCall(
            id=self._state.next_id,
            thread_id=thread_id,
            run_id=run_id,
            tool_call_index=index,
            provider_tool_call_id=provider_id,
            requested_by_model=requested_by_model,
            tool_name=tool_name,
            arguments=dict(arguments),
        )
        self._state.rows[row.id] = row
        self._state.next_id += 1
        return row

    @staticmethod
    def _matches(
        row: _MemoryToolCall,
        *,
        thread_id: int | str,
        requested_by_model: bool,
        tool_name: str,
        arguments: JsonObject,
    ) -> bool:
        return (
            row.thread_id == thread_id
            and row.requested_by_model is requested_by_model
            and row.tool_name == tool_name
            and json_values_equal(row.arguments, arguments)
        )

    async def materialize(
        self,
        *,
        thread_id: int | str,
        run_id: int | str,
        tool_call_index: int,
        provider_tool_call_id: str | None,
        tool_name: str,
        arguments: JsonObject,
    ) -> ToolCallRecord:
        row = self._by_run_index(run_id, tool_call_index)
        provider_row = (
            self._by_run_provider(run_id, provider_tool_call_id)
            if provider_tool_call_id is not None
            else None
        )
        if row is None and provider_row is None:
            return self._insert(
                thread_id=thread_id,
                run_id=run_id,
                index=tool_call_index,
                provider_id=provider_tool_call_id,
                tool_name=tool_name,
                arguments=arguments,
                requested_by_model=True,
            )
        if (
            row is None
            or (provider_row is not None and provider_row.id != row.id)
            or not self._matches(
                row,
                thread_id=thread_id,
                requested_by_model=True,
                tool_name=tool_name,
                arguments=arguments,
            )
        ):
            raise ToolProtocolError("Tool Call index was reused inconsistently")
        return row

    async def materialize_system(
        self,
        *,
        thread_id: int | str,
        run_id: int | str,
        identity: str,
        tool_name: str,
        arguments: JsonObject,
        requested_by_model: bool = False,
    ) -> ToolCallRecord:
        row = self._by_run_provider(run_id, identity)
        if row is None:
            indexes = [
                item.tool_call_index
                for item in self._state.rows.values()
                if item.run_id == run_id
            ]
            return self._insert(
                thread_id=thread_id,
                run_id=run_id,
                index=max(indexes, default=-1) + 1,
                provider_id=identity,
                tool_name=tool_name,
                arguments=arguments,
                requested_by_model=requested_by_model,
            )
        if not self._matches(
            row,
            thread_id=thread_id,
            requested_by_model=requested_by_model,
            tool_name=tool_name,
            arguments=arguments,
        ):
            raise IdempotencyConflictError(identity)
        return row

    async def get(self, tool_call_id: int) -> ToolCallRecord | None:
        return self._state.rows.get(tool_call_id)

    async def get_by_resolution_id(
        self,
        thread_id: int | str,
        client_resolution_id: str,
    ) -> ToolCallRecord | None:
        return next(
            (
                row
                for row in self._state.rows.values()
                if row.thread_id == thread_id
                and row.client_resolution_id == client_resolution_id
            ),
            None,
        )

    async def save_validation(
        self,
        row: ToolCallRecord,
        *,
        interaction_payload: JsonObject,
        guard_payload: JsonObject,
    ) -> bool:
        durable = self._state.rows.get(row.id)
        if durable is None or durable.status != "received":
            return False
        durable.interaction_payload = dict(interaction_payload)
        durable.guard_payload = dict(guard_payload)
        durable.status = "validated"
        durable.updated_at = _now()
        return True

    async def claim_approval_request(self, tool_call_id: int) -> bool:
        return self._transition(tool_call_id, "validated", "awaiting_approval")

    async def claim_input_request(self, tool_call_id: int) -> bool:
        return self._transition(tool_call_id, "validated", "awaiting_input")

    def _transition(self, tool_call_id: int, source: str, target: str) -> bool:
        row = self._state.rows.get(tool_call_id)
        if row is None or row.status != source:
            return False
        row.status = target
        row.updated_at = _now()
        return True

    async def resolve_input(
        self,
        tool_call_id: int,
        *,
        client_resolution_id: str,
        tool_result: JsonObject,
        delivery_status: DeliveryStatus,
    ) -> bool:
        row = self._state.rows.get(tool_call_id)
        if (
            row is None
            or row.status != "awaiting_input"
            or row.client_resolution_id is not None
        ):
            return False
        if await self.get_by_resolution_id(row.thread_id, client_resolution_id):
            return False
        self._finish(
            row,
            decision=None,
            result=tool_result,
            resolution_id=client_resolution_id,
            delivery_status=delivery_status,
        )
        return True

    async def approve(self, tool_call_id: int, client_resolution_id: str) -> bool:
        row = self._state.rows.get(tool_call_id)
        if (
            row is None
            or row.status != "awaiting_approval"
            or row.client_resolution_id is not None
        ):
            return False
        if await self.get_by_resolution_id(row.thread_id, client_resolution_id):
            return False
        row.status = "approved"
        row.decision = "approve"
        row.client_resolution_id = client_resolution_id
        row.updated_at = _now()
        return True

    async def claim_rejection(
        self,
        tool_call_id: int,
        client_resolution_id: str,
    ) -> bool:
        row = self._state.rows.get(tool_call_id)
        if (
            row is None
            or row.status != "awaiting_approval"
            or row.client_resolution_id is not None
        ):
            return False
        if await self.get_by_resolution_id(row.thread_id, client_resolution_id):
            return False
        row.status = "executing"
        row.decision = "reject"
        row.client_resolution_id = client_resolution_id
        row.updated_at = _now()
        return True

    async def resolve(
        self,
        row: ToolCallRecord,
        *,
        decision: str | None,
        tool_result: JsonObject,
        client_resolution_id: str | None = None,
        delivery_status: DeliveryStatus = "pending",
    ) -> None:
        durable = self._state.rows[row.id]
        self._finish(
            durable,
            decision=decision,
            result=tool_result,
            resolution_id=client_resolution_id,
            delivery_status=delivery_status,
        )

    async def resolve_received(
        self,
        tool_call_id: int,
        *,
        tool_result: JsonObject,
        delivery_status: DeliveryStatus = "pending",
    ) -> bool:
        row = self._state.rows.get(tool_call_id)
        if row is None or row.status != "received":
            return False
        self._finish(
            row,
            decision=None,
            result=tool_result,
            resolution_id=None,
            delivery_status=delivery_status,
        )
        return True

    @staticmethod
    def _finish(
        row: _MemoryToolCall,
        *,
        decision: str | None,
        result: JsonObject,
        resolution_id: str | None,
        delivery_status: DeliveryStatus,
    ) -> None:
        now = _now()
        row.status = "resolved"
        row.decision = decision
        row.tool_result = dict(result)
        row.delivery_status = delivery_status
        row.client_resolution_id = resolution_id
        row.resolved_at = now
        row.updated_at = now

    async def claim_execution(
        self,
        tool_call_id: int,
        *,
        from_status: ExecutableStatus,
    ) -> bool:
        return self._transition(tool_call_id, from_status, "executing")

    async def pending_results(
        self,
        thread_id: int | str,
    ) -> list[ToolCallRecord]:
        return [
            row
            for row in sorted(self._state.rows.values(), key=lambda item: item.id)
            if row.thread_id == thread_id
            and row.status == "resolved"
            and row.requested_by_model
            and row.delivery_status == "pending"
        ]

    async def mark_consumed(self, rows: Collection[ToolCallRecord]) -> None:
        now = _now()
        for row in rows:
            durable = self._state.rows.get(row.id)
            if durable is not None and durable.delivery_status == "pending":
                durable.delivery_status = "consumed"
                durable.updated_at = now


@dataclass
class _MemoryToolCallUnitOfWork:
    store: InMemoryToolCallStore
    state: _MemoryState
    calls: _MemoryToolCallPersistence = field(init=False)
    resources: ToolResources = field(default_factory=ToolResources)

    def __post_init__(self) -> None:
        self.calls = _MemoryToolCallPersistence(self.state)

    async def commit(self) -> None:
        self.store._state = deepcopy(self.state)

    async def rollback(self) -> None:
        return None


@dataclass
class InMemoryToolCallStore:
    """适合一次性 Workflow、测试及无需跨进程恢复的工具存储。"""

    _state: _MemoryState = field(default_factory=_MemoryState, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_MemoryToolCallUnitOfWork]:
        async with self._lock:
            yield _MemoryToolCallUnitOfWork(self, deepcopy(self._state))

    async def pending_results(self, thread_id: int | str) -> list[ToolCall]:
        async with self.transaction() as uow:
            rows = await uow.calls.pending_results(thread_id)
            return [tool_call_from_record(row, replayed=True) for row in rows]

    async def consume_results(
        self,
        tool_call_ids: Collection[int],
        *,
        resources: ToolResources | None = None,
    ) -> None:
        del resources
        async with self.transaction() as uow:
            rows = [
                row
                for tool_call_id in set(tool_call_ids)
                if (row := await uow.calls.get(tool_call_id)) is not None
                and row.delivery_status == "pending"
            ]
            if rows:
                await uow.calls.mark_consumed(rows)
                await uow.commit()
