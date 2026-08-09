"""通用 Graph 的输入、运行和审批恢复状态。"""

from typing import Literal, TypedDict

from app.ai_chat.tools.results import PendingToolResult
from app.ai_chat.types import JsonObject


class ApprovalInput(TypedDict):
    """传入恢复后业务图的审批决定与幂等标识。"""

    tool_call_id: int
    decision: Literal["approve", "reject"]
    client_resolution_id: str


class AdapterInput(TypedDict):
    """通用聊天层提供给 Adapter 的统一调用输入。"""

    conversation_id: int
    run_id: int
    subject: JsonObject
    scope: JsonObject
    language: str
    run_kind: str
    tools_enabled: bool
    messages: list[JsonObject]
    pending_tool_results: list[PendingToolResult]
    model_request: JsonObject


class BaseState(TypedDict):
    """所有业务 Graph 的公共状态基础。"""

    conversation_id: int
    run_id: int
    subject: JsonObject
    scope: JsonObject
    run_kind: str
    tools_enabled: bool
    model_request: JsonObject
