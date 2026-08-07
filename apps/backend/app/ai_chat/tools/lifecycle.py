"""不透明业务工具调用的通用持久化和分派。"""

from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from app import database as database_module
from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.streaming.events import AiChatEvent
from app.ai_chat.tools.buffer import AssembledToolCall
from app.ai_chat.tools.handler import ToolContext, ToolHandler
from app.ai_chat.tools.results import ApprovalProposal, ImmediateToolResult
from app.ai_chat.types import JsonObject


@dataclass(frozen=True)
class ToolDispatch:
    """一次完整工具调用后返回给业务图的结果。"""

    tool_call_id: int
    provider_tool_call_id: str | None
    tool_name: str
    result: JsonObject | None
    event: AiChatEvent | None
    awaits_approval: bool


class ToolLifecycle:
    """持久化工具传输状态，并将全部业务语义委托给处理器。"""

    def __init__(self, repositories: RepositoryFactory) -> None:
        """保存无状态且限定在事务范围内的仓储工厂。"""
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
        """校验、持久化并分派一个已完整组装的工具调用。"""
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
        validation = await handler.invoke(context, arguments)

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
        client_resolution_id: str,
    ) -> JsonObject:
        """在同一数据库事务内认领审批、执行业务写入并持久化结果。"""
        async with database_module.db.session() as session:
            repository = self._repositories.create(session).tool_calls
            row = await repository.get(tool_call_id)
            if row is None:
                raise ToolProtocolError("Tool call does not exist")
            if row.status == "resolved":
                if (
                    row.decision != decision
                    or row.client_resolution_id != client_resolution_id
                ):
                    raise ToolProtocolError("Tool call was resolved with another decision")
                return dict(row.tool_result or {})
            claimed = await repository.claim_resolution(
                tool_call_id,
                decision=decision,
                client_resolution_id=client_resolution_id,
            )
            if not claimed:
                await session.refresh(row)
                if (
                    row.status == "resolved"
                    and row.decision == decision
                    and row.client_resolution_id == client_resolution_id
                ):
                    return dict(row.tool_result or {})
                raise ToolProtocolError("Tool call resolution is already claimed")
            await session.refresh(row)
            arguments = handler.arguments_schema.model_validate(row.arguments)
            context = ToolContext(
                conversation_id=row.conversation_id,
                run_id=row.run_id,
                tool_call_id=row.id,
                subject=subject,
                target=target,
                session=session,
            )
            proposal = dict(row.proposal_payload or {})
            guard = dict(row.guard_payload or {})
            result = await handler.resolve(
                context, arguments, proposal, guard, decision
            )
            await repository.resolve(
                row,
                decision=decision,
                tool_result=result.payload,
                client_resolution_id=row.client_resolution_id,
            )
            await session.commit()
            return result.payload
