"""Durable validation boundary for AI Chat Tool Calls."""

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Literal, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.models import AiChatToolCall
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.tools.buffer import AssembledToolCall
from app.ai_chat.tools.handler import ToolContext, ToolHandler
from app.ai_chat.tools.results import (
    ApprovalRequest,
    ApprovedToolCall,
    CompletedToolCall,
    PreparedToolCall,
    ToolCallState,
    ToolResult,
    ValidatedToolCall,
)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True)
class ToolCallService:
    """Binds Tool Handlers and persists their validation outcomes."""

    session_factory: SessionFactory
    repositories: RepositoryFactory
    handlers: Mapping[str, ToolHandler] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Freeze even the default Handler mapping."""
        object.__setattr__(self, "handlers", MappingProxyType(dict(self.handlers)))

    def bind_handlers(self, handlers: Mapping[str, ToolHandler]) -> "ToolCallService":
        """Return a service instance with a fixed Handler mapping."""
        return replace(self, handlers=handlers)

    @property
    def model_handlers(self) -> Mapping[str, ToolHandler]:
        """Expose the exact bound Handler mapping for model schema generation."""
        return self.handlers

    def _handler(self, name: str) -> ToolHandler:
        handler = self.handlers.get(name)
        if handler is None:
            raise ToolProtocolError(f"Unknown tool: {name}")
        return handler

    def _state_from_row(
        self, row: AiChatToolCall, handler: ToolHandler, *, replayed: bool
    ) -> ToolCallState:
        if row.status == "validated":
            if row.proposal_payload is None or row.guard_payload is None:
                raise ToolProtocolError("Validated Tool Call has no trusted payload")
            return PreparedToolCall(row.id, row.tool_name, handler.security)
        if row.status == "awaiting_approval":
            if row.proposal_payload is None or row.guard_payload is None:
                raise ToolProtocolError("Awaiting approval Tool Call has no trusted payload")
            return ApprovalRequest(row.id, row.tool_name, dict(row.proposal_payload))
        if row.status == "approved":
            if row.proposal_payload is None or row.guard_payload is None:
                raise ToolProtocolError("Approved Tool Call has no trusted payload")
            if row.decision != "approve" or not row.client_resolution_id:
                raise ToolProtocolError("Approved Tool Call has no approval identity")
            return ApprovedToolCall(row.id, row.tool_name, row.client_resolution_id)
        if row.status == "resolved":
            if row.tool_result is None:
                raise ToolProtocolError("Resolved Tool Call has no result")
            decision = row.decision
            if decision not in (None, "approve", "reject"):
                raise ToolProtocolError("Resolved Tool Call has an unsupported decision")
            if decision is not None and not row.client_resolution_id:
                raise ToolProtocolError("Resolved Tool Call has no resolution identity")
            return CompletedToolCall(
                row.id,
                row.tool_name,
                dict(row.tool_result),
                cast(Literal["approve", "reject"] | None, decision),
                replayed,
            )
        raise ToolProtocolError(f"Unsupported Tool Call status: {row.status}")

    async def _reload_state(
        self, tool_call_id: int, handler: ToolHandler
    ) -> ToolCallState:
        async with self.session_factory() as session:
            row = await self.repositories.create(session).tool_calls.get(tool_call_id)
            if row is None:
                raise ToolProtocolError("Persisted Tool Call disappeared")
            return self._state_from_row(row, handler, replayed=True)

    async def validate_call(
        self, context: ToolContext, call: AssembledToolCall
    ) -> ToolCallState:
        """Materialize a call, then validate it in a separate durable transaction."""
        handler = self._handler(call.name)

        async with self.session_factory() as session:
            row = await self.repositories.create(session).tool_calls.materialize(
                conversation_id=context.conversation_id,
                run_id=context.run_id,
                tool_call_index=call.index,
                provider_tool_call_id=call.provider_id,
                tool_name=call.name,
                arguments=dict(call.arguments),
            )
            await session.commit()
            tool_call_id = row.id

        async with self.session_factory() as session:
            repository = self.repositories.create(session).tool_calls
            row = await repository.get(tool_call_id)
            if row is None:
                raise ToolProtocolError("Persisted Tool Call disappeared")
            if row.status != "received":
                return self._state_from_row(row, handler, replayed=True)

            validation = await handler.validation(
                replace(context, tool_call_id=tool_call_id, session=session),
                dict(row.arguments),
            )
            if isinstance(validation, ValidatedToolCall):
                saved = await repository.save_validation(
                    row,
                    proposal_payload=dict(validation.proposal_payload),
                    guard_payload=dict(validation.guard_payload),
                )
                if saved:
                    await session.commit()
                    return PreparedToolCall(tool_call_id, row.tool_name, handler.security)
                await session.rollback()
                return await self._reload_state(tool_call_id, handler)
            if isinstance(validation, ToolResult):
                saved = await repository.resolve_received(
                    tool_call_id,
                    tool_result=dict(validation.payload),
                )
                if saved:
                    await session.commit()
                    return CompletedToolCall(
                        tool_call_id,
                        row.tool_name,
                        dict(validation.payload),
                        None,
                        False,
                    )
                await session.rollback()
                return await self._reload_state(tool_call_id, handler)
            raise ToolProtocolError("Tool validation returned an unsupported result")
