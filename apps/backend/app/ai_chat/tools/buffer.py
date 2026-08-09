"""组装模型的工具调用片段。"""

import json
from dataclasses import dataclass

from app.ai_chat.errors import ToolProtocolError


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

    def assemble(self) -> list[str]:
        """模型结束后把完整调用封装成等待校验节点解析的字符串。"""
        calls: list[str] = []
        for index in sorted(self._pending):
            pending = self._pending[index]
            if not pending.name:
                raise ToolProtocolError("Tool Call did not include a name")
            calls.append(
                encode_tool_call(
                    index=index,
                    provider_id=pending.provider_id,
                    name=pending.name,
                    arguments=pending.arguments or "{}",
                )
            )
        return calls


def encode_tool_call(
    *,
    index: int,
    provider_id: str | None,
    name: str,
    arguments: str,
) -> str:
    """把提供方字段封装成统一的原始工具调用字符串。"""
    return json.dumps(
        {
            "index": index,
            "provider_id": provider_id,
            "name": name,
            "arguments": arguments,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
