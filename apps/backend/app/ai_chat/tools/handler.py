"""Protocol implemented by Tool business handlers."""

from abc import ABC, abstractmethod
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

    @abstractmethod
    async def validation(
        self,
        context: ToolContext,
        arguments: JsonObject,
    ) -> ToolValidationResult:
        """Validate model arguments and prepare trusted execution payloads."""

    @abstractmethod
    async def execute(
        self,
        context: ToolContext,
        proposal_payload: JsonObject,
        guard_payload: JsonObject,
    ) -> ToolResult:
        """Execute one prepared operation inside the injected transaction."""

    @abstractmethod
    def show_result(self, payload: JsonObject) -> ToolResult:
        """Normalize a stable business result returned by this Handler."""

    def schema(self) -> dict[str, Any]:
        """Return provider-independent Tool JSON schema."""
        return self.arguments_schema.model_json_schema()
