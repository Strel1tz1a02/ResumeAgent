"""不透明业务工具调用的通用持久化和分派。"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import ValidationError

from app import database as database_module
from app.ai_chat.errors import (
    IdempotencyConflictError,
    ProposalStateError,
    ToolCallNotFoundError,
    ToolProtocolError,
)
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.tools.buffer import AssembledToolCall
from app.ai_chat.tools.handler import ToolContext, ToolHandler
from app.ai_chat.tools.results import ApprovalProposal, ToolResult
from app.ai_chat.types import JsonObject


@dataclass(frozen=True)
class ApprovalRequired:
    """已持久化且正在等待用户审批的 Tool Call。"""

    tool_call_id: int
    proposal_payload: JsonObject


@dataclass(frozen=True)
class ToolCompleted:
    """无需审批或已经解决的 Tool Call。"""

    tool_call_id: int
    result: JsonObject


ToolDispatch = ApprovalRequired | ToolCompleted


class ToolLifecycle:
    """持久化工具传输状态，并将全部业务语义委托给处理器。"""

    def __init__(self, repositories: RepositoryFactory) -> None:
        """保存无状态且限定在事务范围内的仓储工厂。"""
        self._repositories = repositories

    async def receive(
        self,
        *,
        context: ToolContext,
        call: AssembledToolCall,
        handlers: Mapping[str, ToolHandler],
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
            row = await repository.get_by_run_index(context.run_id, call.index)
            if row is None:
                row = await repository.create(
                    conversation_id=context.conversation_id,
                    run_id=context.run_id,
                    tool_call_index=call.index,
                    provider_tool_call_id=call.provider_id,
                    tool_name=call.name,
                    arguments=call.arguments,
                )
                await session.commit()
            elif (row.tool_name != call.name or row.arguments != call.arguments):
                raise ToolProtocolError("Tool Call index was reused inconsistently")
            tool_call_id = row.id
            if row.status == "resolved":
                return ToolCompleted(
                    tool_call_id=tool_call_id,
                    result=dict(row.tool_result or {}),
                )
            if row.status == "awaiting_approval":
                if row.proposal_payload is None:
                    raise ToolProtocolError("Pending approval has no proposal payload")
                return ApprovalRequired(
                    tool_call_id=tool_call_id,
                    proposal_payload=dict(row.proposal_payload),
                )
            if row.status != "received":
                raise ToolProtocolError(f"Unsupported Tool Call status: {row.status}")

        context = replace(
            context,
            tool_call_id=tool_call_id,
            session=None,
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
                return ApprovalRequired(
                    tool_call_id=tool_call_id,
                    proposal_payload=validation.proposal_payload,
                )
            if not isinstance(validation, ToolResult):
                raise ToolProtocolError("Tool Handler returned an unsupported result")
            await repository.resolve(row, decision=None, tool_result=validation.payload)
            await session.commit()
            return ToolCompleted(
                tool_call_id=tool_call_id,
                result=validation.payload,
            )

    async def resolve(
        self,
        *,
        tool_call_id: int,
        decision: Literal["approve", "reject"],
        handlers: Mapping[str, ToolHandler],
        subject: JsonObject,
        scope: JsonObject,
        client_resolution_id: str,
    ) -> JsonObject:
        """在同一数据库事务内认领审批、执行业务写入并持久化结果。"""
        async with database_module.db.session() as session:
            repository = self._repositories.create(session).tool_calls
            row = await repository.get(tool_call_id)
            if row is None:
                raise ToolCallNotFoundError(str(tool_call_id))
            handler = handlers.get(row.tool_name)
            if handler is None:
                raise ToolProtocolError(f"Unknown tool: {row.tool_name}")
            if row.status == "resolved":
                if (row.decision != decision or row.client_resolution_id != client_resolution_id):
                    raise IdempotencyConflictError(client_resolution_id)
                return dict(row.tool_result or {})
            if row.status != "awaiting_approval":
                raise ProposalStateError(str(tool_call_id))
            existing_resolution = await repository.get_by_resolution_id(row.conversation_id, client_resolution_id)
            if existing_resolution is not None and existing_resolution.id != row.id: # 如果 client_resolution_id 已用于另一条 Tool Call
                raise IdempotencyConflictError(client_resolution_id)
            claimed = await repository.claim_resolution(
                tool_call_id,
                decision=decision,
                client_resolution_id=client_resolution_id,
            )
            if not claimed: # 出现别人先写入的情况
                await session.refresh(row)
                if (row.status == "resolved" and row.decision == decision and row.client_resolution_id == client_resolution_id): # 完全相同的审批已经完成
                    return dict(row.tool_result or {})
                raise ProposalStateError(str(tool_call_id))
            await session.refresh(row)
            context = ToolContext(
                conversation_id=row.conversation_id,
                run_id=row.run_id,
                tool_call_id=row.id,
                subject=subject,
                scope=scope,
                session=session,
            )
            result = await handler.resolve(
                context,
                dict(row.proposal_payload or {}),
                dict(row.guard_payload or {}),
                decision,
            )
            await repository.resolve(
                row,
                decision=decision,
                tool_result=result.payload,
                client_resolution_id=row.client_resolution_id,
            )
            await session.commit()
            return result.payload
