"""已注册 LangChain 工具的只读目录。"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from langchain_core.tools import BaseTool

from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.tools.operation import RegisteredTool


@dataclass(frozen=True)
class ToolRegistry:
    """提供工具查找与模型可见工具投影。"""

    tools: Mapping[str, RegisteredTool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """冻结注册表，避免一次运行期间工具集合变化。"""
        object.__setattr__(self, "tools", MappingProxyType(dict(self.tools)))

    def get(self, name: str) -> RegisteredTool:
        """按名称读取工具，未知名称按协议错误处理。"""
        tool = self.tools.get(name)
        if tool is None:
            raise ToolProtocolError(f"Unknown tool: {name}")
        return tool

    @property
    def model_tools(self) -> Mapping[str, BaseTool]:
        """只返回允许暴露给模型的 LangChain 工具。"""
        return MappingProxyType(
            {name: item.tool for name, item in self.tools.items() if item.model_visible}
        )
