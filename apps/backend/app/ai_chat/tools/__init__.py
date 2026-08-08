"""工具协议、风险和结果类型。"""

from app.ai_chat.tools.handler import ToolContext, ToolHandler
from app.ai_chat.tools.results import (
    ApprovalProposal,
    ApprovalRequest,
    ApprovedToolCall,
    CompletedToolCall,
    PendingToolResult,
    PreparedToolCall,
    ToolCallState,
    ToolInvocationResult,
    ToolResult,
    ToolValidationResult,
    ValidatedToolCall,
)
from app.ai_chat.tools.security import GuardDecision, ToolSecurity, guard_tool

__all__ = [
    "ApprovalProposal",
    "ApprovalRequest",
    "ApprovedToolCall",
    "CompletedToolCall",
    "GuardDecision",
    "PendingToolResult",
    "PreparedToolCall",
    "ToolCallState",
    "ToolContext",
    "ToolHandler",
    "ToolInvocationResult",
    "ToolResult",
    "ToolSecurity",
    "ToolValidationResult",
    "ValidatedToolCall",
    "guard_tool",
]
