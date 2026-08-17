"""Persistence and idempotency boundary for durable Tool Calls."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.models import AiChatToolCall
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.repositories.tool_repository import ToolCallRepository
from app.ai_chat.tools.types import (
    ApprovalAction,
    ToolCall,
    ToolCallStatus,
    ToolResult,
)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True)
class ToolCallUnitOfWork:
    """One transaction over the atomic Tool Call state machine."""

    session: AsyncSession
    calls: ToolCallRepository


@dataclass(frozen=True)
class ToolCallStore:
    """Own database access used for materialization, claims and replay."""

    session_factory: SessionFactory
    repositories: RepositoryFactory

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[ToolCallUnitOfWork]:
        async with self.session_factory() as session:
            yield ToolCallUnitOfWork(
                session=session,
                calls=self.repositories.create(session).tool_calls,
            )

    @staticmethod
    def validate_row(row: AiChatToolCall) -> None:
        """Reject durable rows whose state and payloads contradict each other."""
        if not isinstance(row.requested_by_model, bool):
            raise ToolProtocolError("Tool Call has no valid model-origin marker")
        if row.status == "received" and any(
            value is not None
            for value in (
                row.proposal_payload,
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
        } and (row.proposal_payload is None or row.guard_payload is None):
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
                if row.proposal_payload is None or row.guard_payload is None:
                    raise ToolProtocolError(
                        "Resolved approval Tool Call has incomplete trusted state"
                    )
                if (
                    not row.client_resolution_id
                    or not row.client_resolution_id.strip()
                ):
                    raise ToolProtocolError(
                        "Resolved Tool Call has no resolution identity"
                    )
            elif (row.proposal_payload is None) != (row.guard_payload is None):
                raise ToolProtocolError(
                    "Resolved Tool Call has incomplete trusted state"
                )
            if row.delivery_status not in {"pending", "consumed"}:
                raise ToolProtocolError("Resolved Tool Call has no delivery state")
            if not row.requested_by_model and row.delivery_status != "consumed":
                raise ToolProtocolError(
                    "System Tool Call cannot have a pending model delivery"
                )
            if not row.resolved_at or not row.resolved_at.strip():
                raise ToolProtocolError("Resolved Tool Call has no resolved timestamp")
        elif row.delivery_status is not None or row.resolved_at is not None:
            raise ToolProtocolError("Unresolved Tool Call has terminal delivery data")

    @classmethod
    def call_from_row(cls, row: AiChatToolCall, *, replayed: bool) -> ToolCall:
        """Map the durable row to the single graph-facing Tool Call shape."""
        if row.status not in {
            "validated",
            "awaiting_approval",
            "awaiting_input",
            "approved",
            "resolved",
        }:
            raise ToolProtocolError(f"Unsupported Tool Call status: {row.status}")
        cls.validate_row(row)
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
            "proposal_payload": (
                dict(row.proposal_payload)
                if row.proposal_payload is not None
                else None
            ),
            "should_execute": should_execute,
            "result": dict(row.tool_result) if row.tool_result is not None else None,
            "replayed": replayed,
        }

    @classmethod
    def result_from_row(cls, row: AiChatToolCall, *, replayed: bool) -> ToolResult:
        """Map a resolved row to a result carrying its durable identity."""
        cls.validate_row(row)
        if row.status != "resolved" or row.tool_result is None:
            raise ToolProtocolError("Tool Call has no durable result")
        return ToolResult(
            payload=dict(row.tool_result),
            tool_call_id=row.id,
            tool_name=row.tool_name,
            decision=cast(ApprovalAction | None, row.decision),
            replayed=replayed,
        )

    async def load_call(self, tool_call_id: int, *, replayed: bool) -> ToolCall:
        async with self.transaction() as uow:
            row = await uow.calls.get(tool_call_id)
            if row is None:
                raise ToolProtocolError("Persisted Tool Call disappeared")
            return self.call_from_row(row, replayed=replayed)

    async def load_result(self, tool_call_id: int, *, replayed: bool) -> ToolResult:
        async with self.transaction() as uow:
            row = await uow.calls.get(tool_call_id)
            if row is None:
                raise ToolProtocolError("Persisted Tool Call disappeared")
            return self.result_from_row(row, replayed=replayed)
