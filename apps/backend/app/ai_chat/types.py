"""AI 对话边界共享的可序列化类型。"""

from typing import Literal, NotRequired, TypeAlias, TypedDict

from pydantic import BaseModel, ConfigDict, Field

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class SubjectRef(BaseModel):
    """由对话运行时持久化的不透明业务主体引用。"""

    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1, max_length=100)
    id: str = Field(min_length=1, max_length=200)


class TargetRef(BaseModel):
    """由对话运行时持久化的不透明业务目标引用。"""

    model_config = ConfigDict(extra="allow")

    key: str = Field(min_length=1, max_length=200)
    ref_id: int | None = None


class ValidatedBinding(BaseModel):
    """经过业务校验和规范化的会话绑定。"""

    subject: SubjectRef
    target: TargetRef


class AdapterState(BaseModel):
    """具体 Adapter 返回给通用 Graph Runner 的可序列化业务状态。"""

    model_config = ConfigDict(extra="forbid")


class PendingToolResult(TypedDict):
    """等待模型完整响应的不透明工具结果。"""

    tool_call_id: int
    provider_tool_call_id: str | None
    tool_name: str
    arguments: JsonObject
    result: JsonObject


class ApprovalInput(TypedDict):
    """传入恢复后业务图的审批决定和工具结果。"""

    tool_call_id: int
    decision: Literal["approve", "reject"]
    tool_result: JsonObject


class AdapterInput(TypedDict):
    """由具体适配器解析的通用单次运行输入。"""

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
    """在所有适配器中语义一致的可序列化状态字段。"""

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
