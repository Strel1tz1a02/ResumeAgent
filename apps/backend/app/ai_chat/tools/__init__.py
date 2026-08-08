"""工具协议和生命周期辅助类型。"""

from app.ai_chat.tools.handler import ToolContext, ToolHandler
from app.ai_chat.tools.lifecycle import (
    ApprovalRequired,
    ToolCompleted,
    ToolDispatch,
    ToolLifecycle,
)
from app.ai_chat.tools.results import (
    ApprovalProposal,
    PendingToolResult,
    ToolInvocationResult,
    ToolResult,
)

__all__ = [
    "ApprovalProposal",
    "ApprovalRequired",
    "PendingToolResult",
    "ToolCompleted",
    "ToolContext",
    "ToolHandler",
    "ToolInvocationResult",
    "ToolResult",
    "ToolDispatch",
    "ToolLifecycle",
]
