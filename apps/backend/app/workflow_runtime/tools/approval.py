"""工具审批的无状态策略。"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from app.workflow_runtime.tools.operation import ToolRisk
from app.workflow_runtime.types import JsonObject

ApprovalRoute = Literal["execute", "approval"]
ProposalBuilder = Callable[[JsonObject], JsonObject]


@dataclass(frozen=True)
class ToolApprovalPolicy:
    proposal_builders: Mapping[str, ProposalBuilder] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "proposal_builders",
            MappingProxyType(dict(self.proposal_builders)),
        )

    def route(self, risk: ToolRisk) -> ApprovalRoute:
        return "execute" if risk is ToolRisk.LOW else "approval"

    def proposal(self, tool_name: str, prepared_data: JsonObject) -> JsonObject:
        builder = self.proposal_builders.get(tool_name)
        return dict(builder(prepared_data) if builder else prepared_data)
