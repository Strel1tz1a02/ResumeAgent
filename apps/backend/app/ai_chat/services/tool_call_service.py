"""把 Handler 方法与数据库事务、状态流转绑定起来，向 Graph 提供稳定接口"""

import json
import math
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Literal, cast

from pydantic import ValidationError
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
from app.ai_chat.tools.handler import ToolHandler
from app.ai_chat.tools.types import (
    ApprovalAction,
    ApprovalDecision,
    ToolCall,
    ToolCallStatus,
    ToolContext,
    ToolResult,
)
from app.ai_chat.types import JsonObject

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
_SQLITE_INT64_MAX = (1 << 63) - 1


def _reject_json_constant(value: str) -> None:
    """拒绝 Python JSON 解码器默认接受的非标准数字常量。"""
    raise ValueError(f"Unsupported JSON constant: {value}")


def _ensure_finite_json(value: Any, *, message: str) -> None:
    """递归拒绝常量和指数溢出产生的非有限浮点数。"""
    if isinstance(value, float) and not math.isfinite(value):
        raise ToolProtocolError(message)
    if isinstance(value, dict):
        for item in value.values():
            _ensure_finite_json(item, message=message)
    elif isinstance(value, list):
        for item in value:
            _ensure_finite_json(item, message=message)


@dataclass(frozen=True)
class ToolCallService:
    """绑定处理器，并协调工具调用的持久化状态流转。"""

    session_factory: SessionFactory
    repositories: RepositoryFactory
    handlers: Mapping[str, ToolHandler] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """冻结包括默认值在内的处理器映射。"""
        object.__setattr__(self, "handlers", MappingProxyType(dict(self.handlers)))

    def bind_handlers(self, handlers: Mapping[str, ToolHandler]) -> "ToolCallService":
        """返回绑定固定处理器映射的新服务实例。"""
        return replace(self, handlers=handlers)

    @property
    def model_handlers(self) -> Mapping[str, ToolHandler]:
        """向模型结构生成逻辑暴露当前绑定的处理器。"""
        return self.handlers

    def _handler(self, name: str) -> ToolHandler:
        handler = self.handlers.get(name)
        if handler is None:
            raise ToolProtocolError(f"Unknown tool: {name}")
        return handler

    @staticmethod
    def _parse_model_call(raw_call: str) -> tuple[int, str | None, str, JsonObject]:
        """解析模型的原始字符串，并校验通用调用外壳。"""
        if not isinstance(raw_call, str):
            raise ToolProtocolError("Tool Call must be a string")
        try:
            envelope: Any = json.loads(
                raw_call,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, RecursionError, ValueError) as exc:
            raise ToolProtocolError("Tool Call is not valid JSON") from exc
        if not isinstance(envelope, dict):
            raise ToolProtocolError("Tool Call must be a JSON object")
        _ensure_finite_json(
            envelope,
            message="Tool Call contains a non-finite number",
        )

        index = envelope.get("index")
        provider_id = envelope.get("provider_id")
        name = envelope.get("name")
        raw_arguments = envelope.get("arguments")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or index > _SQLITE_INT64_MAX
        ):
            raise ToolProtocolError("Tool Call index is outside the supported range")
        if provider_id is not None and not isinstance(provider_id, str):
            raise ToolProtocolError("Tool Call provider id must be a string")
        if not isinstance(name, str) or not name.strip():
            raise ToolProtocolError("Tool Call did not include a name")
        if not isinstance(raw_arguments, str):
            raise ToolProtocolError("Tool Call arguments must be a string")
        try:
            arguments: Any = json.loads(
                raw_arguments or "{}",
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, RecursionError, ValueError) as exc:
            raise ToolProtocolError("Tool Call arguments are not valid JSON") from exc
        if not isinstance(arguments, dict):
            raise ToolProtocolError("Tool Call arguments must be a JSON object")
        _ensure_finite_json(
            arguments,
            message="Tool Call arguments contain a non-finite number",
        )
        return index, provider_id, name, cast(JsonObject, arguments)

    @staticmethod
    def _validate_row(row: AiChatToolCall) -> None:
        """校验数据库状态与可选字段是否一致。"""
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
        if row.status in {"validated", "awaiting_approval", "approved"}:
            if row.proposal_payload is None or row.guard_payload is None:
                raise ToolProtocolError("Tool Call has no trusted payload")
        if row.status in {"validated", "awaiting_approval"}:
            if (
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
            if row.decision is None and row.client_resolution_id is not None:
                raise ToolProtocolError("Resolved Tool Call has an orphan resolution identity")
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
            if not row.resolved_at or not row.resolved_at.strip():
                raise ToolProtocolError("Resolved Tool Call has no resolved timestamp")
        elif row.delivery_status is not None or row.resolved_at is not None:
            raise ToolProtocolError("Unresolved Tool Call has terminal delivery data")

    def _call_from_row(
        self,
        row: AiChatToolCall,
        handler: ToolHandler,
        *,
        replayed: bool,
    ) -> ToolCall:
        """把持久化记录映射成唯一的工具调用结构。"""
        if row.status not in {
            "validated",
            "awaiting_approval",
            "approved",
            "resolved",
        }:
            raise ToolProtocolError(f"Unsupported Tool Call status: {row.status}")
        self._validate_row(row)
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
            "name": row.tool_name,
            "arguments": dict(row.arguments),
            "status": status,
            "security": handler.security.value,
            "proposal_payload": (
                dict(row.proposal_payload)
                if row.proposal_payload is not None
                else None
            ),
            "should_execute": should_execute,
            "result": dict(row.tool_result) if row.tool_result is not None else None,
            "replayed": replayed,
        }

    @staticmethod
    def _result_from_row(
        row: AiChatToolCall,
        *,
        replayed: bool,
    ) -> ToolResult:
        """从已完成记录生成带持久化身份的工具结果。"""
        ToolCallService._validate_row(row)
        if row.status != "resolved" or row.tool_result is None:
            raise ToolProtocolError("Tool Call has no durable result")
        return ToolResult(
            payload=dict(row.tool_result),
            tool_call_id=row.id,
            tool_name=row.tool_name,
            decision=cast(ApprovalAction | None, row.decision),
            replayed=replayed,
        )

    async def _reload_call(
        self,
        tool_call_id: int,
        handler: ToolHandler,
        *,
        replayed: bool,
    ) -> ToolCall:
        """使用新事务重新读取唯一持久化真相。"""
        async with self.session_factory() as session:
            row = await self.repositories.create(session).tool_calls.get(tool_call_id)
            if row is None:
                raise ToolProtocolError("Persisted Tool Call disappeared")
            return self._call_from_row(row, handler, replayed=replayed)

    async def _reload_result(
        self,
        tool_call_id: int,
        *,
        replayed: bool,
    ) -> ToolResult:
        """使用新事务重新读取已固化的工具结果。"""
        async with self.session_factory() as session:
            row = await self.repositories.create(session).tool_calls.get(tool_call_id)
            if row is None:
                raise ToolProtocolError("Persisted Tool Call disappeared")
            return self._result_from_row(row, replayed=replayed)

    async def get_call(self, tool_call_id: int) -> ToolCall:
        """为图恢复流程加载统一的持久化工具调用。"""
        async with self.session_factory() as session:
            row = await self.repositories.create(session).tool_calls.get(tool_call_id)
            if row is None:
                raise ToolCallNotFoundError(str(tool_call_id))
            return self._call_from_row(
                row,
                self._handler(row.tool_name),
                replayed=True,
            )

    async def validate_call(self, context: ToolContext, raw_call: str) -> ToolCall:
        """解析并校验模型字符串，然后固化处理器的可信执行依据。"""
        index, provider_id, name, raw_arguments = self._parse_model_call(raw_call)
        handler = self._handler(name)

        async with self.session_factory() as session:
            row = await self.repositories.create(session).tool_calls.materialize(
                conversation_id=context.conversation_id,
                run_id=context.run_id,
                tool_call_index=index,
                provider_tool_call_id=provider_id,
                tool_name=name,
                arguments=dict(raw_arguments),
            )
            await session.commit()
            tool_call_id = row.id

        async with self.session_factory() as session:
            repository = self.repositories.create(session).tool_calls
            row = await repository.get(tool_call_id)
            if row is None:
                raise ToolProtocolError("Persisted Tool Call disappeared")
            if row.status != "received":
                return self._call_from_row(row, handler, replayed=True)
            self._validate_row(row)

            try:
                values = handler.arguments_schema.model_validate(row.arguments)
            except ValidationError as exc:
                raise ToolProtocolError(f"Invalid arguments for tool {name}") from exc
            arguments = cast(JsonObject, values.model_dump(mode="json"))
            _ensure_finite_json(
                arguments,
                message=f"Invalid arguments for tool {name}: non-finite number",
            )

            validation = await handler.validation(
                replace(context, tool_call_id=tool_call_id, session=session),
                arguments,
            )
            if isinstance(validation, ToolResult):
                _ensure_finite_json(
                    validation.payload,
                    message="Tool validation returned a non-finite result",
                )
                saved = await repository.resolve_received(
                    tool_call_id,
                    tool_result=dict(validation.payload),
                )
            elif (
                isinstance(validation, tuple)
                and len(validation) == 2
                and isinstance(validation[0], dict)
                and isinstance(validation[1], dict)
            ):
                _ensure_finite_json(
                    validation[0],
                    message="Tool validation returned a non-finite proposal",
                )
                _ensure_finite_json(
                    validation[1],
                    message="Tool validation returned a non-finite guard",
                )
                saved = await repository.save_validation(
                    row,
                    proposal_payload=dict(validation[0]),
                    guard_payload=dict(validation[1]),
                )
            else:
                raise ToolProtocolError(
                    "Tool validation returned an unsupported result"
                )
            if saved:
                await session.commit()
                return await self._reload_call(
                    tool_call_id,
                    handler,
                    replayed=False,
                )
            await session.rollback()

        return await self._reload_call(tool_call_id, handler, replayed=True)

    async def request_approval(self, tool_call_id: int) -> ToolCall:
        """持久化审批申请，但不替审批策略作出决定。"""
        async with self.session_factory() as session:
            repository = self.repositories.create(session).tool_calls
            row = await repository.get(tool_call_id)
            if row is None:
                raise ToolCallNotFoundError(str(tool_call_id))
            handler = self._handler(row.tool_name)
            call = self._call_from_row(row, handler, replayed=True)
            if call["status"] in {"awaiting_approval", "approved", "resolved"}:
                return call
            if call["status"] != "validated":
                raise ToolProtocolError("Tool Call cannot request approval")
            claimed = await repository.claim_approval_request(tool_call_id)
            if claimed:
                await session.commit()
            else:
                await session.rollback()

        durable = await self._reload_call(
            tool_call_id,
            handler,
            replayed=not claimed,
        )
        if durable["status"] not in {
            "awaiting_approval",
            "approved",
            "resolved",
        }:
            raise ToolProtocolError("Tool Call approval request was not persisted")
        return durable

    async def _reload_decision(self, approval: ApprovalDecision) -> ToolCall:
        """原子状态竞争失败后，只根据最新持久化快照映射审批结果。"""
        tool_call_id = approval["tool_call_id"]
        resolution_id = approval["client_resolution_id"]
        async with self.session_factory() as session:
            repository = self.repositories.create(session).tool_calls
            row = await repository.get(tool_call_id)
            if row is None:
                raise ToolCallNotFoundError(str(tool_call_id))
            owner = await repository.get_by_resolution_id(
                row.conversation_id,
                resolution_id,
            )
            if owner is not None and owner.id != tool_call_id:
                raise IdempotencyConflictError(resolution_id)
            handler = self._handler(row.tool_name)
            call = self._call_from_row(row, handler, replayed=True)
            if call["status"] not in {"approved", "resolved"}:
                raise ProposalStateError(str(tool_call_id))
            if (
                row.decision != approval["decision"]
                or row.client_resolution_id != resolution_id
            ):
                raise IdempotencyConflictError(resolution_id)
            return call

    async def record_decision(self, approval: ApprovalDecision) -> ToolCall:
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
            call = self._call_from_row(row, handler, replayed=True)
            if call["status"] in {"approved", "resolved"}:
                if (
                    row.decision != decision
                    or row.client_resolution_id != resolution_id
                ):
                    raise IdempotencyConflictError(resolution_id)
                return call
            if call["status"] != "awaiting_approval":
                raise ProposalStateError(str(tool_call_id))

            owner = await repository.get_by_resolution_id(
                row.conversation_id,
                resolution_id,
            )
            if owner is not None and owner.id != tool_call_id:
                raise IdempotencyConflictError(resolution_id)
            try:
                if decision == "approve":
                    claimed = await repository.approve(tool_call_id, resolution_id)
                else:
                    claimed = await repository.claim_rejection(
                        tool_call_id,
                        resolution_id,
                    )
                    if claimed:
                        await repository.resolve(
                            row,
                            decision="reject",
                            tool_result={"outcome": "rejected"},
                            client_resolution_id=resolution_id,
                        )
                if claimed:
                    await session.commit()
                else:
                    await session.rollback()
            except IntegrityError:
                await session.rollback()
                claimed = False

        if claimed:
            return await self._reload_call(
                tool_call_id,
                handler,
                replayed=False,
            )
        return await self._reload_decision(approval)

    async def execute_call(
        self,
        context: ToolContext,
        tool_call_id: int,
    ) -> ToolResult:
        """根据数据库事实认领、执行并提交工具结果。"""
        async with self.session_factory() as session:
            repository = self.repositories.create(session).tool_calls
            row = await repository.get(tool_call_id)
            if row is None:
                raise ToolCallNotFoundError(str(tool_call_id))
            handler = self._handler(row.tool_name)
            durable = self._call_from_row(row, handler, replayed=True)
            if (
                context.conversation_id != row.conversation_id
                or context.run_id != row.run_id
            ):
                raise ToolProtocolError("Tool Call context identity does not match")
            if durable["status"] == "resolved":
                return self._result_from_row(row, replayed=True)
            if durable["status"] not in {"validated", "approved"}:
                raise ToolProtocolError("Tool Call is not ready for execution")

            from_status: Literal["validated", "approved"] = (
                "approved" if durable["status"] == "approved" else "validated"
            )
            proposal_payload = dict(row.proposal_payload or {})
            guard_payload = dict(row.guard_payload or {})
            decision = cast(ApprovalAction | None, row.decision)
            resolution_id = row.client_resolution_id
            claimed = await repository.claim_execution(
                tool_call_id,
                from_status=from_status,
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
                    _ensure_finite_json(
                        payload,
                        message="Tool execute returned a non-finite result",
                    )
                    await repository.resolve(
                        row,
                        decision=decision,
                        tool_result=payload,
                        client_resolution_id=resolution_id,
                    )
                    await session.commit()
                    return ToolResult(
                        payload=payload,
                        tool_call_id=tool_call_id,
                        tool_name=row.tool_name,
                        decision=decision,
                        replayed=False,
                    )
                except Exception:
                    await session.rollback()
                    raise

        return await self._reload_result(tool_call_id, replayed=True)
