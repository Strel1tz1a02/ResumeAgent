"""所有业务 AI 适配器都要实现的抽象边界。"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import TYPE_CHECKING, Generic, TypeVar

from langgraph.graph import StateGraph

from app.ai_chat.types import AdapterInput, ScopeRef, SubjectRef, ValidatedBinding

if TYPE_CHECKING:
    from app.ai_chat.graph.runtime import AiChatRuntime
    from app.ai_chat.tools.approval import ToolApprovalPolicy
    from app.ai_chat.tools.operation import RegisteredTool


StateT = TypeVar("StateT")


class BaseAdapter(ABC, Generic[StateT]):
    """将通用对话输入转换为无状态业务图。"""

    @classmethod
    def adapter_name(cls) -> str:
        """返回持久化到会话和注册表中的稳定名称。"""
        return cls.__name__

    @abstractmethod
    async def validate_request(
        self,
        subject: SubjectRef,
        scope: ScopeRef,
    ) -> ValidatedBinding:
        """检查指定业务位置是否允许启用会话，并返回规范化绑定。"""

    @abstractmethod
    async def parse_input(self, value: AdapterInput) -> StateT:
        """把统一调用输入转换成具体业务 Graph 的完整状态。"""

    @abstractmethod
    def build_graph(self, runtime: "AiChatRuntime") -> StateGraph:
        """返回尚未编译的业务图定义。"""

    @abstractmethod
    def get_tools(self) -> Mapping[str, "RegisteredTool"]:
        """返回此业务注册的 LangChain 工具。"""

    @abstractmethod
    def get_tool_approval_policy(self) -> "ToolApprovalPolicy":
        """返回此业务的无状态工具审批策略。"""
