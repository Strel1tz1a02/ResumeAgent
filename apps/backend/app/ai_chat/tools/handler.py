"""工具业务处理器必须实现的协议。"""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from app.ai_chat.tools.security import ToolSecurity
from app.ai_chat.tools.types import ToolContext, ToolResult
from app.ai_chat.types import JsonObject


class ToolHandler(ABC):
    """封装工具的校验、执行和结果整理。"""

    name: str
    description: str
    arguments_schema: type[BaseModel]
    security: ToolSecurity
    model_visible: bool = True
    deliver_result_to_model: bool = True

    @abstractmethod
    async def validation(
        self,
        context: ToolContext,
        arguments: JsonObject,
    ) -> tuple[JsonObject, JsonObject] | ToolResult:
        """校验模型参数，并准备可信执行载荷。"""

    @abstractmethod
    async def execute(
        self,
        context: ToolContext,
        proposal_payload: JsonObject,
        guard_payload: JsonObject,
    ) -> ToolResult:
        """在注入的事务中执行已准备的操作。"""

    @abstractmethod
    def show_result(self, payload: JsonObject) -> ToolResult:
        """整理并返回稳定的业务结果。"""

    def schema(self) -> dict[str, Any]:
        """返回与模型供应商无关的工具参数 JSON 模式。"""
        return self.arguments_schema.model_json_schema()
