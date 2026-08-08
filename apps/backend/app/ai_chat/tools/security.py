"""Tool 风险到审批动作的统一映射。"""

from enum import Enum
from typing import Literal


class ToolSecurity(str, Enum):
    """描述 Tool 固有风险；是否审批由 Graph guard 决定。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


GuardDecision = Literal["execute", "approval"]


def guard_tool(security: ToolSecurity) -> GuardDecision:
    """根据 Tool 固有风险决定直接执行或等待人工审批。"""
    if security is ToolSecurity.LOW:
        return "execute"
    return "approval"
