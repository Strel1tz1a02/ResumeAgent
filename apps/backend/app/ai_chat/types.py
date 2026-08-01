"""Serializable types shared across the AI Chat boundary."""

from typing import Literal, NotRequired, TypeAlias, TypedDict

from pydantic import BaseModel, ConfigDict, Field

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class SubjectRef(BaseModel):
    """Opaque business subject reference persisted by the chat runtime."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1, max_length=100)
    id: str = Field(min_length=1, max_length=200)


class TargetRef(BaseModel):
    """Opaque business target reference persisted by the chat runtime."""

    model_config = ConfigDict(extra="allow")

    key: str = Field(min_length=1, max_length=200)
    ref_id: int | None = None


class ValidatedBinding(BaseModel):
    """Business-validated and normalized conversation binding."""

    subject: SubjectRef
    target: TargetRef


class PendingToolResult(TypedDict):
    """Opaque Tool Result awaiting a complete model response."""

    tool_call_id: int
    provider_tool_call_id: str | None
    tool_name: str
    arguments: JsonObject
    result: JsonObject


class ApprovalInput(TypedDict):
    """Decision and Tool Result passed into a resumed Graph."""

    tool_call_id: int
    decision: Literal["approve", "reject"]
    tool_result: JsonObject


class AdapterInput(TypedDict):
    """Common per-run input parsed by a concrete Adapter."""

    conversation_id: int
    run_id: int
    adapter: str
    subject: JsonObject
    target: JsonObject
    language: str
    run_kind: str
    tools_enabled: bool
    messages: list[JsonObject]
    pending_tool_results: list[PendingToolResult]
    user_message_id: NotRequired[int]
    approval: NotRequired[ApprovalInput]


class AiChatBaseState(TypedDict):
    """Serializable State fields with identical meaning for every Adapter."""

    conversation_id: int
    run_id: int
    adapter: str
    subject: JsonObject
    target: JsonObject
    language: str
    run_kind: str
    tools_enabled: bool
    messages: list[JsonObject]
    pending_tool_results: list[PendingToolResult]
    user_message_id: NotRequired[int]
    approval: NotRequired[ApprovalInput]
