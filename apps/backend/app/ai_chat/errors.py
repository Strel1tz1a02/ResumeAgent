"""Conversation 层错误；通用执行错误由 workflow_runtime 定义。"""

from app.workflow_runtime.errors import (
    IdempotencyConflictError,
    InteractionStateError,
    RunInProgressError,
    ToolCallNotFoundError,
    ToolProtocolError,
    WorkflowRuntimeError,
)

AiChatError = WorkflowRuntimeError


class ConversationNotFoundError(AiChatError):
    """会话不存在时抛出。"""

    code = "conversation_not_found"


class ConversationEndedError(AiChatError):
    """尝试运行已结束会话时抛出。"""

    code = "conversation_ended"


__all__ = [
    "AiChatError",
    "ConversationEndedError",
    "ConversationNotFoundError",
    "IdempotencyConflictError",
    "InteractionStateError",
    "RunInProgressError",
    "ToolCallNotFoundError",
    "ToolProtocolError",
]
