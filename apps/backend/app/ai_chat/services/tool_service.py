"""连接独立工具模块的薄门面。"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from langchain_core.messages import ToolCall as LangChainToolCall
from langchain_core.tools import BaseTool

from app.ai_chat.tools.approval import ToolApprovalPolicy, ToolApprovalService
from app.ai_chat.tools.delivery import ToolResultDeliveryService
from app.ai_chat.tools.execution import ToolExecutionService
from app.ai_chat.tools.input import ToolInputService
from app.ai_chat.tools.operation import RegisteredTool
from app.ai_chat.tools.preparation import ToolCallPreparationService
from app.ai_chat.tools.query import ToolCallQueryService
from app.ai_chat.tools.registry import ToolRegistry
from app.ai_chat.tools.store import ToolCallStore
from app.ai_chat.tools.types import ApprovalDecision, ToolCall, ToolContext, ToolResult
from app.ai_chat.types import JsonObject


@dataclass(frozen=True)
class ToolService:
    """保留稳定入口，并把每项功能委托给对应独立模块。"""

    store: ToolCallStore
    tools: Mapping[str, RegisteredTool] = field(default_factory=dict)
    approval_policy: ToolApprovalPolicy = field(default_factory=ToolApprovalPolicy)

    @property
    def registry(self) -> ToolRegistry:
        """返回当前服务绑定的只读工具注册表。"""
        return ToolRegistry(self.tools)

    @property
    def approval(self) -> ToolApprovalService:
        """返回绑定当前依赖的审批生命周期模块。"""
        return ToolApprovalService(self.store, self.registry, self.approval_policy)

    @property
    def preparation(self) -> ToolCallPreparationService:
        """返回绑定当前依赖的调用准备模块。"""
        return ToolCallPreparationService(self.store, self.registry, self.approval)

    @property
    def inputs(self) -> ToolInputService:
        """返回绑定当前依赖的外部输入模块。"""
        return ToolInputService(self.store, self.registry)

    @property
    def executor(self) -> ToolExecutionService:
        """返回绑定当前依赖的执行模块。"""
        return ToolExecutionService(self.store, self.registry)

    @property
    def queries(self) -> ToolCallQueryService:
        """返回绑定当前依赖的调用查询模块。"""
        return ToolCallQueryService(self.store, self.registry)

    @property
    def delivery(self) -> ToolResultDeliveryService:
        """返回工具结果投递状态模块。"""
        return ToolResultDeliveryService(self.store)

    def bind_tools(
        self,
        tools: Mapping[str, RegisteredTool],
        approval_policy: ToolApprovalPolicy,
    ) -> "ToolService":
        """返回绑定固定工具和审批模块的新门面。"""
        return replace(
            self,
            tools=ToolRegistry(tools).tools,
            approval_policy=approval_policy,
        )

    @property
    def model_tools(self) -> Mapping[str, BaseTool]:
        """返回允许暴露给模型的 LangChain 工具。"""
        return self.registry.model_tools

    async def get_call(self, tool_call_id: int) -> ToolCall:
        """加载持久化调用，并确认对应工具仍已注册。"""
        return await self.queries.get(tool_call_id)

    async def validate_call(
        self,
        context: ToolContext,
        model_call: LangChainToolCall,
        *,
        index: int = 0,
    ) -> ToolCall:
        """委托准备模块处理完整模型调用。"""
        return await self.preparation.validate_call(context, model_call, index=index)

    async def validate_system_call(
        self,
        context: ToolContext,
        *,
        identity: str,
        name: str,
        arguments: JsonObject,
        requested_by_model: bool = False,
    ) -> ToolCall:
        """委托准备模块处理服务端稳定调用。"""
        return await self.preparation.validate_system_call(
            context,
            identity=identity,
            name=name,
            arguments=arguments,
            requested_by_model=requested_by_model,
        )

    async def validate_model_call_as(
        self,
        context: ToolContext,
        model_call: LangChainToolCall,
        *,
        identity: str,
        expected_name: str,
    ) -> ToolCall:
        """委托准备模块用稳定身份固化模型调用。"""
        return await self.preparation.validate_model_call_as(
            context,
            model_call,
            identity=identity,
            expected_name=expected_name,
        )

    async def request_approval(self, tool_call_id: int) -> ToolCall:
        """委托审批模块持久化审批申请。"""
        return await self.approval.request(tool_call_id)

    async def record_decision(self, approval: ApprovalDecision) -> ToolCall:
        """委托审批模块持久化审批决定。"""
        return await self.approval.record_decision(approval)

    async def request_input(self, tool_call_id: int) -> ToolCall:
        """委托外部输入模块创建等待状态。"""
        return await self.inputs.request(tool_call_id)

    async def find_awaiting_input(self, run_id: int) -> ToolCall | None:
        """委托外部输入模块查找等待中的调用。"""
        return await self.inputs.find_awaiting(run_id)

    async def resolve_input(
        self,
        tool_call_id: int,
        client_resolution_id: str,
        payload: JsonObject,
    ) -> ToolResult:
        """委托外部输入模块固化输入结果。"""
        return await self.inputs.resolve(tool_call_id, client_resolution_id, payload)

    async def get_call_by_run_identity(
        self,
        run_id: int,
        identity: str,
    ) -> ToolCall:
        """按服务端稳定身份加载调用。"""
        return await self.queries.get_by_run_identity(run_id, identity)

    async def consume_result(self, tool_call_id: int) -> None:
        """在模型成功接收结果后持久化消费状态。"""
        await self.delivery.consume(tool_call_id)

    async def execute_call(
        self,
        context: ToolContext,
        tool_call_id: int,
    ) -> ToolResult:
        """委托执行模块认领并执行调用。"""
        return await self.executor.execute(context, tool_call_id)
