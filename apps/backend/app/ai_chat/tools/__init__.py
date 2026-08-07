"""工具协议和生命周期辅助类型。"""

from app.ai_chat.tools.handler import ToolContext, ToolHandler
from app.ai_chat.tools.lifecycle import ToolDispatch, ToolLifecycle
from app.ai_chat.tools.results import (
    ApprovalProposal,
    ImmediateToolResult,
    PendingToolResult,
    ToolResult,
    ToolValidation,
)

__all__ = [
    "ApprovalProposal",
    "ImmediateToolResult",
    "PendingToolResult",
    "ToolContext",
    "ToolHandler",
    "ToolResult",
    "ToolValidation",
    "ToolDispatch",
    "ToolLifecycle",
]
