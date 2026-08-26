"""已注册模型工具的只读目录。"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from langchain_core.tools import BaseTool

from app.workflow_runtime.errors import ToolProtocolError
from app.workflow_runtime.tools.operation import RegisteredTool


@dataclass(frozen=True)
class ToolRegistry:
    tools: Mapping[str, RegisteredTool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tools", MappingProxyType(dict(self.tools)))

    def get(self, name: str) -> RegisteredTool:
        tool = self.tools.get(name)
        if tool is None:
            raise ToolProtocolError(f"Unknown tool: {name}")
        return tool

    @property
    def model_tools(self) -> Mapping[str, BaseTool]:
        return MappingProxyType(
            {name: item.tool for name, item in self.tools.items() if item.model_visible}
        )

