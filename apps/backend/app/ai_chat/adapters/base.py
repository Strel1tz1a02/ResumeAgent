"""所有业务 AI 适配器都要实现的抽象边界。"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import TYPE_CHECKING

from langgraph.graph import StateGraph

from app.ai_chat.graph.state import AdapterInput, BaseState
from app.ai_chat.types import ScopeRef, SubjectRef, ValidatedBinding

if TYPE_CHECKING:
    from app.ai_chat.graph.runtime import AiChatRuntime
    from app.ai_chat.tools.handler import ToolHandler


class BaseAdapter(ABC): # ABC：要求子类必须实现父类要求的方法
    """将通用对话输入转换为无状态业务图。"""

    @classmethod # @classmethod：这个方法属于“类”
    def adapter_name(cls) -> str: # cls：当前类
        """返回持久化到会话和注册表中的稳定名称。"""
        return cls.__name__

    @abstractmethod # @abstractmethod：子类必须实现这个方法
    async def validate_binding(self, subject: SubjectRef, scope: ScopeRef) -> ValidatedBinding:
        """在持久化前校验并规范化业务绑定。"""

    @abstractmethod
    async def parse_input(self, value: AdapterInput) -> BaseState:
        """把统一调用输入转换成具体业务 Graph 的完整状态。"""

    @abstractmethod
    def build_graph(self, runtime: "AiChatRuntime") -> StateGraph:
        """返回尚未编译的业务图定义。"""

    @abstractmethod
    def get_tool_handlers(self) -> Mapping[str, "ToolHandler"]:
        """返回此业务可以使用的工具包。"""
