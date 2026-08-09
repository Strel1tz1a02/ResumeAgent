"""AI Chat 工具调用的持久化编排边界。"""

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Literal, TypedDict, cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.errors import (
    IdempotencyConflictError,
    ProposalStateError,
    ToolCallNotFoundError,
    ToolProtocolError,
)
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


class ApprovalDecision(TypedDict):
    """ToolCallService 边界中已持久化的审批选择。"""

    tool_call_id: int
    decision: Literal["approve", "reject"]
    client_resolution_id: str


@dataclass(frozen=True)
class ToolCallService:
    """绑定 Tool Handler，并协调工具调用的持久化状态流转。"""

    session_factory: SessionFactory
    repositories: RepositoryFactory
    handlers: Mapping[str, ToolHandler] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """冻结包括默认值在内的 Handler 映射。"""
        object.__setattr__(self, "handlers", MappingProxyType(dict(self.handlers)))

    def bind_handlers(self, handlers: Mapping[str, ToolHandler]) -> "ToolCallService":
        """返回绑定固定 Handler 映射的新服务实例。"""
        return replace(self, handlers=handlers)

    @property
    def model_handlers(self) -> Mapping[str, ToolHandler]:
        """向模型结构生成逻辑暴露当前绑定的 Handler 映射。"""
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
            if (
                row.tool_result is not None
                or row.decision is not None
                or row.client_resolution_id is not None
            ):
                raise ToolProtocolError("Validated Tool Call has terminal state data")
            return PreparedToolCall(row.id, row.tool_name, handler.security)
        if row.status == "awaiting_approval":
            if row.proposal_payload is None or row.guard_payload is None:
                raise ToolProtocolError("Awaiting approval Tool Call has no trusted payload")
            if (
                row.tool_result is not None
                or row.decision is not None
                or row.client_resolution_id is not None
            ):
                raise ToolProtocolError(
                    "Awaiting approval Tool Call has resolution state data"
                )
            return ApprovalRequest(row.id, row.tool_name, dict(row.proposal_payload))
        if row.status == "approved":
            if row.proposal_payload is None or row.guard_payload is None:
                raise ToolProtocolError("Approved Tool Call has no trusted payload")
            if row.tool_result is not None:
                raise ToolProtocolError("Approved Tool Call already has a result")
            if (
                row.decision != "approve"
                or not row.client_resolution_id
                or not row.client_resolution_id.strip()
            ):
                raise ToolProtocolError("Approved Tool Call has no approval identity")
            return ApprovedToolCall(row.id, row.tool_name, row.client_resolution_id)
        if row.status == "resolved":
            if row.tool_result is None:
                raise ToolProtocolError("Resolved Tool Call has no result")
            decision = row.decision
            if decision not in (None, "approve", "reject"):
                raise ToolProtocolError("Resolved Tool Call has an unsupported decision")
            if decision is None:
                if row.client_resolution_id is not None:
                    raise ToolProtocolError(
                        "Resolved Tool Call has an orphan resolution identity"
                    )
            elif row.proposal_payload is None or row.guard_payload is None:
                raise ToolProtocolError(
                    "Resolved approval Tool Call has incomplete trusted state"
                )
            elif (
                not row.client_resolution_id
                or not row.client_resolution_id.strip()
            ):
                raise ToolProtocolError(
                    "Resolved Tool Call has no resolution identity"
                )
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
        """先固化工具调用，再在独立事务中校验。"""
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
            elif isinstance(validation, ToolResult):
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
            else:
                raise ToolProtocolError(
                    "Tool validation returned an unsupported result"
                )

        return await self._reload_state(tool_call_id, handler)

    async def request_approval(
        self, tool_call_id: int
    ) -> ApprovalRequest | ApprovedToolCall | CompletedToolCall:
        """持久化审批请求，但不替审批策略作出决定。"""
        async with self.session_factory() as session:
            repository = self.repositories.create(session).tool_calls
            row = await repository.get(tool_call_id)
            if row is None:
                raise ToolCallNotFoundError(str(tool_call_id))
            handler = self._handler(row.tool_name)
            state = self._state_from_row(row, handler, replayed=True)
            if isinstance(state, (ApprovalRequest, ApprovedToolCall, CompletedToolCall)):
                return state
            if not isinstance(state, PreparedToolCall):
                raise ToolProtocolError("Tool Call cannot request approval")

            claimed = await repository.claim_approval_request(tool_call_id)
            if claimed:
                await session.commit()
                return ApprovalRequest(
                    row.id,
                    row.tool_name,
                    dict(row.proposal_payload or {}),
                )
            await session.rollback()

        durable = await self._reload_state(tool_call_id, handler)
        if isinstance(durable, (ApprovalRequest, ApprovedToolCall, CompletedToolCall)):
            return durable
        raise ToolProtocolError("Tool Call approval request was not persisted")

    async def _reload_decision(
        self, approval: ApprovalDecision
    ) -> ApprovedToolCall | CompletedToolCall:
        """CAS 竞争失败后，只根据最新持久化快照映射审批结果。"""
        tool_call_id = approval["tool_call_id"]
        resolution_id = approval["client_resolution_id"]
        async with self.session_factory() as session:
            repository = self.repositories.create(session).tool_calls
            row = await repository.get(tool_call_id)
            if row is None:
                raise ToolCallNotFoundError(str(tool_call_id))
            handler = self._handler(row.tool_name)
            state = self._state_from_row(row, handler, replayed=True)
            owner = await repository.get_by_resolution_id(
                row.conversation_id, resolution_id
            )
            if owner is not None and owner.id != tool_call_id:
                raise IdempotencyConflictError(resolution_id)
            if isinstance(state, (ApprovedToolCall, CompletedToolCall)):
                if (
                    row.decision != approval["decision"]
                    or row.client_resolution_id != resolution_id
                ):
                    raise IdempotencyConflictError(resolution_id)
                return state
            raise ProposalStateError(str(tool_call_id))

    async def record_decision(
        self, approval: ApprovalDecision
    ) -> ApprovedToolCall | CompletedToolCall:
        """在执行前持久化外部审批决定。"""
        tool_call_id = approval["tool_call_id"]
        decision = approval["decision"]
        resolution_id = approval["client_resolution_id"]
        if decision not in ("approve", "reject"):
            raise ToolProtocolError("Unsupported approval decision")
        if not isinstance(resolution_id, str) or not resolution_id.strip():
            raise ToolProtocolError("Approval resolution id must be non-empty")

        async with self.session_factory() as session:
            repository = self.repositories.create(session).tool_calls
            row = await repository.get(tool_call_id)
            if row is None:
                raise ToolCallNotFoundError(str(tool_call_id))
            handler = self._handler(row.tool_name)
            state = self._state_from_row(row, handler, replayed=True)
            if isinstance(state, (ApprovedToolCall, CompletedToolCall)):
                if (
                    row.decision != decision
                    or row.client_resolution_id != resolution_id
                ):
                    raise IdempotencyConflictError(resolution_id)
                return state
            if not isinstance(state, ApprovalRequest):
                raise ProposalStateError(str(tool_call_id))

            owner = await repository.get_by_resolution_id(
                row.conversation_id, resolution_id
            )
            if owner is not None and owner.id != tool_call_id:
                raise IdempotencyConflictError(resolution_id)
            try:
                if decision == "approve":
                    claimed = await repository.approve(tool_call_id, resolution_id)
                    if claimed:
                        await session.commit()
                        return ApprovedToolCall(
                            tool_call_id, row.tool_name, resolution_id
                        )
                else:
                    claimed = await repository.claim_rejection(
                        tool_call_id, resolution_id
                    )
                    if claimed:
                        result = {"outcome": "rejected"}
                        await repository.resolve(
                            row,
                            decision="reject",
                            tool_result=result,
                            client_resolution_id=resolution_id,
                        )
                        await session.commit()
                        return CompletedToolCall(
                            tool_call_id,
                            row.tool_name,
                            result,
                            "reject",
                            False,
                        )
            except IntegrityError:
                await session.rollback()
            else:
                await session.rollback()

        return await self._reload_decision(approval)

    async def execute_call(
        self, context: ToolContext, tool_call_id: int
    ) -> CompletedToolCall:
        """在同一事务中认领、执行并完成已校验的工具调用。"""
        async with self.session_factory() as session:
            repository = self.repositories.create(session).tool_calls
            row = await repository.get(tool_call_id)
            if row is None:
                raise ToolCallNotFoundError(str(tool_call_id))
            handler = self._handler(row.tool_name)
            state = self._state_from_row(row, handler, replayed=True)
            if (
                context.conversation_id != row.conversation_id
                or context.run_id != row.run_id
            ):
                raise ToolProtocolError("Tool Call context identity does not match")
            if isinstance(state, CompletedToolCall):
                return state
            if not isinstance(state, (PreparedToolCall, ApprovedToolCall)):
                raise ToolProtocolError("Tool Call is not ready for execution")

            from_status: Literal["validated", "approved"] = (
                "approved" if isinstance(state, ApprovedToolCall) else "validated"
            )
            proposal_payload = dict(row.proposal_payload or {})
            guard_payload = dict(row.guard_payload or {})
            decision = cast(Literal["approve"] | None, row.decision)
            resolution_id = row.client_resolution_id
            claimed = await repository.claim_execution(
                tool_call_id, from_status=from_status
            )
            if not claimed:
                await session.rollback()
            else:
                try:
                    result = await handler.execute(
                        replace(
                            context,
                            conversation_id=row.conversation_id,
                            run_id=row.run_id,
                            tool_call_id=tool_call_id,
                            session=session,
                        ),
                        proposal_payload,
                        guard_payload,
                    )
                    if not isinstance(result, ToolResult):
                        raise ToolProtocolError(
                            "Tool execute returned an unsupported result"
                        )
                    payload = dict(result.payload)
                    await repository.resolve(
                        row,
                        decision=decision,
                        tool_result=payload,
                        client_resolution_id=resolution_id,
                    )
                    await session.commit()
                    return CompletedToolCall(
                        tool_call_id,
                        row.tool_name,
                        payload,
                        decision,
                        False,
                    )
                except Exception:
                    await session.rollback()
                    raise

        durable = await self._reload_state(tool_call_id, handler)
        if isinstance(durable, CompletedToolCall):
            return durable
        raise ToolProtocolError("Tool Call execution was not durably resolved")
