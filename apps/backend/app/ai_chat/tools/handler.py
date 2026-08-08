"""Protocol implemented by Tool business handlers."""

from abc import ABC
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.tools.results import ToolResult, ToolValidationResult
from app.ai_chat.tools.security import ToolSecurity
from app.ai_chat.types import JsonObject


@dataclass(frozen=True)
class ToolContext:
    """Trusted identity and transaction bindings supplied to a Handler."""

    conversation_id: int
    run_id: int
    subject: JsonObject
    scope: JsonObject
    adapter_context: JsonObject = field(default_factory=dict)
    tool_call_id: int | None = None
    session: AsyncSession | None = None


class ToolHandler(ABC):
    """Encapsulate a Tool's validation, execution, and result rendering."""

    name: str
    description: str
    arguments_schema: type[BaseModel]
    security: ToolSecurity

    async def validation(
        self,
        context: ToolContext,
        arguments: JsonObject,
    ) -> ToolValidationResult:
        """Use the old ``invoke`` protocol only while staged callers remain."""
        legacy_invoke = getattr(self, "invoke", None)
        if legacy_invoke is None:
            raise NotImplementedError("Tool Handler must implement validation")
        values = self.arguments_schema.model_validate(arguments)
        return await legacy_invoke(context, values)

    async def execute(
        self,
        context: ToolContext,
        proposal_payload: JsonObject,
        guard_payload: JsonObject,
    ) -> ToolResult:
        """Use the old approved-only ``resolve`` protocol during migration."""
        legacy_resolve = getattr(self, "resolve", None)
        if legacy_resolve is None:
            raise NotImplementedError("Tool Handler must implement execute")
        return await legacy_resolve(
            context,
            proposal_payload,
            guard_payload,
            "approve",
        )

    def show_result(self, payload: JsonObject) -> ToolResult:
        """Normalize a legacy or current Handler result."""
        return ToolResult(dict(payload))

    def schema(self) -> dict[str, Any]:
        """Return provider-independent Tool JSON schema."""
        return self.arguments_schema.model_json_schema()
