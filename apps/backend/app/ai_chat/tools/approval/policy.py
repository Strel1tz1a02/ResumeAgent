"""工具审批的无状态策略定义。"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Literal

from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.tools.types import ToolCall
from app.ai_chat.types import JsonObject


class ToolRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


ApprovalRoute = Literal["execute", "approval"]
ProposalBuilder = Callable[[JsonObject], JsonObject]


@dataclass(frozen=True)
class ToolApprovalPolicy:
    """声明工具风险及准备数据的审批展示规则。"""

    risks: Mapping[str, ToolRisk] = field(default_factory=dict)
    proposal_builders: Mapping[str, ProposalBuilder] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """冻结策略，避免运行期间发生变化。"""
        object.__setattr__(self, "risks", MappingProxyType(dict(self.risks)))
        object.__setattr__(
            self,
            "proposal_builders",
            MappingProxyType(dict(self.proposal_builders)),
        )

    def risk(self, tool_name: str) -> ToolRisk:
        """返回工具风险，未配置工具按协议错误处理。"""
        try:
            return self.risks[tool_name]
        except KeyError as exc:
            raise ToolProtocolError(
                f"Tool has no approval policy: {tool_name}"
            ) from exc

    def route(self, call: ToolCall) -> ApprovalRoute:
        """根据完整调用选择执行或审批路由。"""
        return "execute" if self.risk(call["name"]) is ToolRisk.LOW else "approval"

    def proposal(self, tool_name: str, prepared_data: JsonObject) -> JsonObject:
        """从准备数据中选择允许展示给审批界面的内容。"""
        builder = self.proposal_builders.get(tool_name)
        return dict(builder(prepared_data) if builder else prepared_data)
