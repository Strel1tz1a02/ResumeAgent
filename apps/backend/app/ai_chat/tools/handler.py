"""Business Tool Handler protocol with opaque results."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from app.ai_chat.types import JsonObject


@dataclass(frozen=True)
class ToolContext:
    """Identifiers and opaque binding supplied to Tool business logic."""

    conversation_id: int
    run_id: int
    tool_call_id: int | None
    subject: JsonObject
    target: JsonObject
    adapter_context: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalProposal:
    """Opaque proposal that must be approved before execution."""

    proposal_payload: JsonObject
    guard_payload: JsonObject


@dataclass(frozen=True)
class ImmediateToolResult:
    """Opaque Tool Result that does not require user approval."""

    payload: JsonObject


@dataclass(frozen=True)
class ToolResult:
    """Opaque result produced after an approval decision."""

    payload: JsonObject


ToolValidation = ApprovalProposal | ImmediateToolResult


class ToolHandler(ABC):
    """Validate and resolve one business Tool without leaking its semantics."""

    name: str
    arguments_schema: type[BaseModel]

    @abstractmethod
    async def validate(
        self, context: ToolContext, arguments: BaseModel
    ) -> ToolValidation:
        """Return either an approval proposal or an immediate opaque result."""

    @abstractmethod
    async def resolve(
        self,
        context: ToolContext,
        arguments: BaseModel,
        proposal_payload: JsonObject,
        guard_payload: JsonObject,
        decision: Literal["approve", "reject"],
    ) -> ToolResult:
        """Resolve an approved or rejected proposal through business logic."""

    def schema(self) -> dict[str, Any]:
        """Return the provider-neutral JSON Schema for this Tool."""
        return self.arguments_schema.model_json_schema()
