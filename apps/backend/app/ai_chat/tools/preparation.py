"""完整 LangChain ToolCall 的校验、业务准备与固化。"""

from dataclasses import dataclass, replace
from typing import cast

from langchain_core.messages import ToolCall as LangChainToolCall
from pydantic import ValidationError

from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.tools.approval.service import ToolApprovalService
from app.ai_chat.tools.json_validation import ensure_finite_json
from app.ai_chat.tools.registry import ToolRegistry
from app.ai_chat.tools.store import ToolCallStore
from app.ai_chat.tools.types import ToolCall, ToolContext, ToolResult
from app.ai_chat.types import JsonObject

_SQLITE_INT64_MAX = (1 << 63) - 1


@dataclass(frozen=True)
class ToolCallPreparationService:
    """把完整模型或系统调用准备成可审批、可执行的持久化调用。"""

    store: ToolCallStore
    registry: ToolRegistry
    approval: ToolApprovalService

    @staticmethod
    def validate_model_call(
        model_call: LangChainToolCall,
    ) -> tuple[str | None, str, JsonObject]:
        """校验流聚合层交付的完整 LangChain ToolCall。"""
        if not isinstance(model_call, dict):
            raise ToolProtocolError("Tool Call must be a complete LangChain ToolCall")
        provider_id = model_call.get("id")
        name = model_call.get("name")
        arguments = model_call.get("args")
        if provider_id is not None and not isinstance(provider_id, str):
            raise ToolProtocolError("Tool Call provider id must be a string")
        if not isinstance(name, str) or not name.strip():
            raise ToolProtocolError("Tool Call did not include a name")
        if not isinstance(arguments, dict):
            raise ToolProtocolError("Complete Tool Call arguments must be an object")
        ensure_finite_json(
            arguments,
            message="Tool Call arguments contain a non-finite number",
        )
        return provider_id, name, cast(JsonObject, arguments)

    @staticmethod
    def validate_call_index(index: int) -> None:
        """校验聚合层提供的调用顺序可被持久化。"""
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or index > _SQLITE_INT64_MAX
        ):
            raise ToolProtocolError("Tool Call index is outside the supported range")

    async def validate_call(
        self,
        context: ToolContext,
        model_call: LangChainToolCall,
        *,
        index: int = 0,
    ) -> ToolCall:
        """固化并准备一个由模型产生的完整调用。"""
        self.validate_call_index(index)
        provider_id, name, arguments = self.validate_model_call(model_call)
        tool = self.registry.get(name)
        async with self.store.transaction() as uow:
            row = await uow.calls.materialize(
                conversation_id=context.conversation_id,
                run_id=context.run_id,
                tool_call_index=index,
                provider_tool_call_id=provider_id,
                tool_name=name,
                arguments=dict(arguments),
            )
            await uow.session.commit()
            tool_call_id = row.id
        return await self._prepare_materialized(context, tool_call_id, tool.name)

    async def validate_system_call(
        self,
        context: ToolContext,
        *,
        identity: str,
        name: str,
        arguments: JsonObject,
        requested_by_model: bool = False,
    ) -> ToolCall:
        """使用 Run 内稳定身份固化并准备服务端调用。"""
        if not identity.strip():
            raise ToolProtocolError("Stable Tool Call identity must be non-empty")
        self.registry.get(name)
        ensure_finite_json(arguments, message="Tool Call contains a non-finite number")
        async with self.store.transaction() as uow:
            row = await uow.calls.materialize_system(
                conversation_id=context.conversation_id,
                run_id=context.run_id,
                identity=identity,
                tool_name=name,
                arguments=dict(arguments),
                requested_by_model=requested_by_model,
            )
            await uow.session.commit()
            tool_call_id = row.id
        return await self._prepare_materialized(context, tool_call_id, name)

    async def validate_model_call_as(
        self,
        context: ToolContext,
        model_call: LangChainToolCall,
        *,
        identity: str,
        expected_name: str,
    ) -> ToolCall:
        """保留模型参数，以服务端稳定身份固化模型调用。"""
        _provider_id, name, arguments = self.validate_model_call(model_call)
        if name != expected_name:
            raise ToolProtocolError(f"Expected Tool Call {expected_name}, got {name}")
        return await self.validate_system_call(
            context,
            identity=identity,
            name=expected_name,
            arguments=arguments,
            requested_by_model=True,
        )

    async def _prepare_materialized(
        self,
        context: ToolContext,
        tool_call_id: int,
        tool_name: str,
    ) -> ToolCall:
        """对已固化调用至多执行一次 Schema 校验和业务准备。"""
        tool = self.registry.get(tool_name)
        async with self.store.transaction() as uow:
            row = await uow.calls.get(tool_call_id)
            if row is None:
                raise ToolProtocolError("Persisted Tool Call disappeared")
            if row.status != "received":
                return self.store.call_from_row(row, replayed=True)
            self.store.validate_row(row)
            try:
                values = tool.tool.get_input_schema().model_validate(row.arguments)
            except ValidationError as exc:
                raise ToolProtocolError(
                    f"Invalid arguments for tool {tool_name}"
                ) from exc
            arguments = cast(JsonObject, values.model_dump(mode="json"))
            ensure_finite_json(
                arguments,
                message=f"Invalid arguments for tool {tool_name}: non-finite number",
            )
            prepared = await tool.prepare(
                replace(context, tool_call_id=tool_call_id, session=uow.session),
                arguments,
            )
            if isinstance(prepared, ToolResult):
                ensure_finite_json(
                    prepared.payload,
                    message="Tool preparation returned a non-finite result",
                )
                saved = await uow.calls.resolve_received(
                    tool_call_id,
                    tool_result=dict(prepared.payload),
                    delivery_status=(
                        "pending" if row.requested_by_model else "consumed"
                    ),
                )
            elif isinstance(prepared, dict):
                ensure_finite_json(
                    prepared,
                    message="Tool preparation returned non-finite data",
                )
                proposal = self.approval.proposal(tool_name, prepared)
                ensure_finite_json(
                    proposal,
                    message="Tool approval projection returned a non-finite proposal",
                )
                saved = await uow.calls.save_validation(
                    row,
                    proposal_payload=proposal,
                    guard_payload=dict(prepared),
                )
            else:
                raise ToolProtocolError("Tool preparation returned an unsupported result")
            if saved:
                await uow.session.commit()
                return await self.store.load_call(tool_call_id, replayed=False)
            await uow.session.rollback()
        return await self.store.load_call(tool_call_id, replayed=True)
