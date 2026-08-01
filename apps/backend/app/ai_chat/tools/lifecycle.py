"""Generic persistence and dispatch for opaque business Tool Calls."""

from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from app import database as database_module
from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.streaming.events import AiChatEvent
from app.ai_chat.tools.buffer import AssembledToolCall
from app.ai_chat.tools.handler import (
    ApprovalProposal,
    ImmediateToolResult,
    ToolContext,
    ToolHandler,
)
from app.ai_chat.types import JsonObject


@dataclass(frozen=True)
class ToolDispatch:
    """Result returned to a business Graph after one complete Tool Call."""

    tool_call_id: int
    provider_tool_call_id: str | None
    tool_name: str
    result: JsonObject | None
    event: AiChatEvent | None
    awaits_approval: bool


class ToolLifecycle:
    """Persist Tool transport state while delegating all meaning to a Handler."""

    def __init__(self, repositories: RepositoryFactory) -> None:
        """Keep the stateless transaction-scoped repository factory."""
        self._repositories = repositories

    async def receive(
        self,
        *,
        conversation_id: int,
        run_id: int,
        subject: JsonObject,
        target: JsonObject,
        call: AssembledToolCall,
        handlers: dict[str, ToolHandler],
        adapter_context: JsonObject,
    ) -> ToolDispatch:
        """Validate, persist, and dispatch one fully assembled Tool Call."""
        handler = handlers.get(call.name)
        if handler is None:
            raise ToolProtocolError(f"Unknown tool: {call.name}")
        try:
            arguments = handler.arguments_schema.model_validate(call.arguments)
        except ValidationError as exc:
            raise ToolProtocolError(f"Invalid arguments for tool {call.name}") from exc

        async with database_module.db.session() as session:
            repository = self._repositories.create(session).tool_calls
            row = (
                await repository.get_by_provider_id(run_id, call.provider_id)
                if call.provider_id is not None
                else None
            )
            if row is None:
                row = await repository.create(
                    conversation_id=conversation_id,
                    run_id=run_id,
                    provider_tool_call_id=call.provider_id,
                    tool_name=call.name,
                    arguments=call.arguments,
                )
                await session.commit()
            elif row.tool_name != call.name or row.arguments != call.arguments:
                raise ToolProtocolError("Provider Tool Call ID was reused inconsistently")
            tool_call_id = row.id
            if row.proposal_payload is not None:
                return ToolDispatch(
                    tool_call_id=tool_call_id,
                    provider_tool_call_id=call.provider_id,
                    tool_name=call.name,
                    result=None,
                    event=AiChatEvent(
                        "proposal.requested",
                        {
                            "proposal_id": tool_call_id,
                            "tool_name": call.name,
                            "proposal": dict(row.proposal_payload),
                        },
                    ),
                    awaits_approval=True,
                )
            if row.status == "resolved":
                return ToolDispatch(
                    tool_call_id=tool_call_id,
                    provider_tool_call_id=call.provider_id,
                    tool_name=call.name,
                    result=dict(row.tool_result or {}),
                    event=None,
                    awaits_approval=False,
                )

        context = ToolContext(
            conversation_id=conversation_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            subject=subject,
            target=target,
            adapter_context=adapter_context,
        )
        validation = await handler.validate(context, arguments)

        async with database_module.db.session() as session:
            repository = self._repositories.create(session).tool_calls
            row = await repository.get(tool_call_id)
            if row is None:
                raise ToolProtocolError("Persisted tool call disappeared")
            if isinstance(validation, ApprovalProposal):
                await repository.request_approval(
                    row,
                    proposal_payload=validation.proposal_payload,
                    guard_payload=validation.guard_payload,
                )
                await session.commit()
                return ToolDispatch(
                    tool_call_id=tool_call_id,
                    provider_tool_call_id=call.provider_id,
                    tool_name=call.name,
                    result=None,
                    event=AiChatEvent(
                        "proposal.requested",
                        {
                            "proposal_id": tool_call_id,
                            "tool_name": call.name,
                            "proposal": validation.proposal_payload,
                        },
                    ),
                    awaits_approval=True,
                )
            if not isinstance(validation, ImmediateToolResult):
                raise ToolProtocolError("Tool Handler returned an unsupported result")
            await repository.resolve(row, decision=None, tool_result=validation.payload)
            await session.commit()
            return ToolDispatch(
                tool_call_id=tool_call_id,
                provider_tool_call_id=call.provider_id,
                tool_name=call.name,
                result=validation.payload,
                event=None,
                awaits_approval=False,
            )

    async def resolve(
        self,
        *,
        tool_call_id: int,
        decision: Literal["approve", "reject"],
        handler: ToolHandler,
        subject: JsonObject,
        target: JsonObject,
    ) -> JsonObject:
        """Invoke business resolution and return its opaque Tool Result."""
        async with database_module.db.session() as session:
            row = await self._repositories.create(session).tool_calls.get(tool_call_id)
            if row is None:
                raise ToolProtocolError("Tool call does not exist")
            arguments = handler.arguments_schema.model_validate(row.arguments)
            context = ToolContext(
                conversation_id=row.conversation_id,
                run_id=row.run_id,
                tool_call_id=row.id,
                subject=subject,
                target=target,
            )
            proposal = dict(row.proposal_payload or {})
            guard = dict(row.guard_payload or {})
        result = await handler.resolve(
            context, arguments, proposal, guard, decision
        )
        return result.payload
