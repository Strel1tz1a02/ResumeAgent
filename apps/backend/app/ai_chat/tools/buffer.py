"""在校验前组装模型提供方的工具调用参数片段。"""

import json
from dataclasses import dataclass
from typing import Any

from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.types import JsonObject


@dataclass(frozen=True)
class AssembledToolCall:
    """一个完整的模型提供方工具调用。"""

    index: int
    provider_id: str | None
    name: str
    arguments: JsonObject


@dataclass
class _PendingToolCall:
    provider_id: str | None = None
    name: str = ""
    arguments: str = ""


class ToolCallBuffer:
    """按模型提供方索引缓冲交错到达的工具片段。"""

    def __init__(self) -> None:
        """创建按模型提供方索引组织的空片段缓冲区。"""
        self._pending: dict[int, _PendingToolCall] = {}

    def add(
        self,
        *,
        index: int,
        provider_id: str | None,
        name: str | None,
        arguments: str | None,
    ) -> None:
        """追加一个片段，但不向调用方暴露中间状态。"""
        pending = self._pending.setdefault(index, _PendingToolCall())
        if provider_id:
            pending.provider_id = provider_id
        if name:
            pending.name += name
        if arguments:
            pending.arguments += arguments

    def assemble(self) -> list[AssembledToolCall]:
        """模型结束后原子解析全部已缓冲调用。"""
        calls: list[AssembledToolCall] = []
        for index in sorted(self._pending):
            pending = self._pending[index]
            if not pending.name:
                raise ToolProtocolError("Tool Call did not include a name")
            try:
                raw: Any = json.loads(pending.arguments or "{}")
            except json.JSONDecodeError as error:
                raise ToolProtocolError("Tool Call arguments are not valid JSON") from error
            if not isinstance(raw, dict):
                raise ToolProtocolError("Tool Call arguments must be a JSON object")
            calls.append(
                AssembledToolCall(
                    index=index,
                    provider_id=pending.provider_id,
                    name=pending.name,
                    arguments=raw,
                )
            )
        return calls
