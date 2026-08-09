"""Tool 业务 Handler 必须实现的协议。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.tools.results import ToolResult, ToolValidationResult
from app.ai_chat.tools.security import ToolSecurity
from app.ai_chat.types import JsonObject


@dataclass(frozen=True)
class ToolContext:
    """Service 提供给 Handler 的可信身份与事务绑定。"""

    conversation_id: int
    run_id: int
    subject: JsonObject
    scope: JsonObject
    adapter_context: JsonObject = field(default_factory=dict)
    tool_call_id: int | None = None
    session: AsyncSession | None = None


class ToolHandler(ABC):
    """封装 Tool 的校验、执行和结果整理。"""

    name: str
    description: str
    arguments_schema: type[BaseModel]
    security: ToolSecurity

    @abstractmethod
    async def validation(
        self,
        context: ToolContext,
        arguments: JsonObject,
    ) -> ToolValidationResult:
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
        """返回与模型供应商无关的 Tool JSON Schema。"""
        return self.arguments_schema.model_json_schema()
