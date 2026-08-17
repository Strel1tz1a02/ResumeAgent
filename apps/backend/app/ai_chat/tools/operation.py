"""LangChain Tool definitions and their application-side preparation hooks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.tools.types import ToolContext, ToolResult
from app.ai_chat.types import JsonObject

_EXECUTION_CONFIG_KEY = "ai_chat_tool_execution"


@dataclass(frozen=True)
class ToolExecution:
    """Trusted inputs injected by ToolService when a Tool is executed."""

    context: ToolContext
    prepared_data: JsonObject


class ToolOperation(ABC):
    """Application behavior associated with a LangChain Tool definition."""

    name: str
    description: str
    args_schema: type[BaseModel]

    @abstractmethod
    async def prepare(
        self,
        context: ToolContext,
        arguments: JsonObject,
    ) -> JsonObject | ToolResult:
        """查询并返回执行前需要的业务数据。"""

    @abstractmethod
    async def execute(
        self,
        context: ToolContext,
        prepared_data: JsonObject,
    ) -> ToolResult:
        """使用下层固化后的准备数据执行操作。"""


@dataclass(frozen=True)
class RegisteredTool:
    """Connect one LangChain Tool to non-model-facing application behavior."""

    operation: ToolOperation
    model_visible: bool = True
    tool: BaseTool = field(init=False)

    def __post_init__(self) -> None:
        operation = self.operation

        async def invoke(config: RunnableConfig, **_arguments: Any) -> ToolResult:
            configurable = config.get("configurable", {})
            execution = configurable.get(_EXECUTION_CONFIG_KEY)
            if not isinstance(execution, ToolExecution):
                raise ToolProtocolError("Tool execution requires ToolService context")
            return await operation.execute(
                execution.context,
                execution.prepared_data,
            )

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
