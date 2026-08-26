"""模型工具定义及其应用侧执行钩子。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from app.workflow_runtime.errors import ToolProtocolError
from app.workflow_runtime.tools.types import ToolContext, ToolResult
from app.workflow_runtime.types import JsonObject

_EXECUTION_CONFIG_KEY = "workflow_tool_execution"


@dataclass(frozen=True)
class ToolExecution:
    context: ToolContext
    prepared_data: JsonObject


class ToolRisk(str, Enum):
    """工具定义声明的固有副作用风险。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolOperation(ABC):
    name: str
    description: str
    args_schema: type[BaseModel]
    risk: ToolRisk

    @abstractmethod
    async def prepare(
        self,
        context: ToolContext,
        arguments: JsonObject,
    ) -> JsonObject | ToolResult: ...

    @abstractmethod
    async def execute(
        self,
        context: ToolContext,
        prepared_data: JsonObject,
    ) -> ToolResult: ...


@dataclass(frozen=True)
class RegisteredTool:
    operation: ToolOperation
    model_visible: bool = True
    tool: BaseTool = field(init=False)

    def __post_init__(self) -> None:
        operation = self.operation
        if not isinstance(getattr(operation, "risk", None), ToolRisk):
            raise ToolProtocolError(
                f"Tool must declare a valid risk: {operation.name}"
            )

        async def invoke(config: RunnableConfig, **_arguments: Any) -> ToolResult:
            configurable = config.get("configurable", {})
            execution = configurable.get(_EXECUTION_CONFIG_KEY)
            if not isinstance(execution, ToolExecution):
                raise ToolProtocolError(
                    "Tool execution requires ToolLifecycle context"
                )
            return await operation.execute(execution.context, execution.prepared_data)

        object.__setattr__(
            self,
            "tool",
            StructuredTool.from_function(
                coroutine=invoke,
                name=operation.name,
                description=operation.description.strip(),
                args_schema=operation.args_schema,
                infer_schema=False,
            ),
        )

    @property
    def name(self) -> str:
        return self.tool.name

    @property
    def risk(self) -> ToolRisk:
        return self.operation.risk

    async def prepare(
        self,
        context: ToolContext,
        arguments: JsonObject,
    ) -> JsonObject | ToolResult:
        return await self.operation.prepare(context, arguments)

    async def invoke(
        self,
        arguments: JsonObject,
        execution: ToolExecution,
    ) -> ToolResult:
        result = await self.tool.ainvoke(
            arguments,
            config={"configurable": {_EXECUTION_CONFIG_KEY: execution}},
        )
        if not isinstance(result, ToolResult):
            raise ToolProtocolError("Tool returned an unsupported result")
        return cast(ToolResult, result)
