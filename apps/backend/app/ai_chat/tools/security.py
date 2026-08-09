"""工具风险到审批动作的统一映射。"""

from enum import Enum
from typing import Literal


class ToolSecurity(str, Enum):
    """描述工具固有风险；是否审批由图中的审批策略节点决定。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


GuardDecision = Literal["execute", "approval"]


def guard_tool(security: ToolSecurity) -> GuardDecision:
    """根据工具固有风险决定直接执行或等待人工审批。"""
    if security is ToolSecurity.LOW:
        return "execute"
    return "approval"
