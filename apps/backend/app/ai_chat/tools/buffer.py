"""Assemble provider Tool Call argument fragments before validation."""

import json
from dataclasses import dataclass
from typing import Any

from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.types import JsonObject


@dataclass(frozen=True)
class AssembledToolCall:
    """One complete provider Tool Call."""

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
    """Buffer interleaved Tool fragments by provider index."""

    def __init__(self) -> None:
        """Create an empty provider-indexed fragment buffer."""
        self._pending: dict[int, _PendingToolCall] = {}

    def add(
        self,
        *,
        index: int,
        provider_id: str | None,
        name: str | None,
        arguments: str | None,
    ) -> None:
        """Append one fragment without exposing it to callers."""
        pending = self._pending.setdefault(index, _PendingToolCall())
        if provider_id:
            pending.provider_id = provider_id
        if name:
            pending.name += name
        if arguments:
            pending.arguments += arguments

    def assemble(self) -> list[AssembledToolCall]:
        """Parse every buffered call atomically after the model finishes."""
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
