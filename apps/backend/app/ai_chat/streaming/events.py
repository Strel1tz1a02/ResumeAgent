"""供所有 Agent Runtime 调用方消费的统一事件。"""

import re
from dataclasses import dataclass

from app.ai_chat.types import JsonObject

_EVENT_TYPE = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*")


@dataclass(frozen=True)
class RuntimeEvent:
    """一个带稳定信封字段的 Runtime 事件。"""

    type: str
    payload: JsonObject
    run_id: int | str | None = None
    sequence: int | None = None

    def __post_init__(self) -> None:
        """拒绝无法形成稳定 Runtime 信封的事件。"""
        if (
            not isinstance(self.type, str)
            or len(self.type) > 120
            or _EVENT_TYPE.fullmatch(self.type) is None
        ):
            raise ValueError("Runtime event type has an invalid format")
        if not isinstance(self.payload, dict):
            raise TypeError("Runtime event payload must be an object")
        if self.run_id is not None:
            valid_integer = (
                isinstance(self.run_id, int)
                and not isinstance(self.run_id, bool)
                and self.run_id > 0
            )
            valid_string = isinstance(self.run_id, str) and bool(self.run_id.strip())
            if not valid_integer and not valid_string:
                raise ValueError("Runtime event run id must be a positive integer or string")
        if self.sequence is not None and (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence <= 0
        ):
            raise ValueError("Runtime event sequence must be a positive integer")

    def bind(self, *, run_id: int | str, sequence: int | None = None) -> "RuntimeEvent":
        """补充 Runtime 拥有的 Run 身份和可选流序号。"""
        return RuntimeEvent(
            type=self.type,
            payload=dict(self.payload),
            run_id=run_id,
            sequence=sequence,
        )

    def envelope(self) -> JsonObject:
        """返回前端和其他传输层共享的 JSON 事件信封。"""
        value: JsonObject = {
            "type": self.type,
            "payload": dict(self.payload),
        }
        if self.run_id is not None:
            value["run_id"] = self.run_id
        if self.sequence is not None:
            value["sequence"] = self.sequence
        return value


def run_event(
    event_type: str,
    run_id: int | str,
    payload: JsonObject | None = None,
) -> RuntimeEvent:
    """构造由 Runtime 生命周期拥有的事件。"""
    return RuntimeEvent(event_type, payload or {}, run_id=run_id)


def output_delta_event(text: str) -> RuntimeEvent:
    """构造统一模型文本增量事件。"""
    return RuntimeEvent("output.delta", {"text": text})


def interaction_requested_event(
    *, interaction_id: int, kind: str, payload: JsonObject
) -> RuntimeEvent:
    """把 Graph waiting Outcome 映射为统一交互请求事件。"""
    return RuntimeEvent(
        "interaction.requested",
        {
            "interaction_id": interaction_id,
            "kind": kind,
            "request": dict(payload),
        },
    )


def interaction_resolved_event(
    *, interaction_id: int, kind: str, outcome: str
) -> RuntimeEvent:
    """构造统一交互解决事件。"""
    return RuntimeEvent(
        "interaction.resolved",
        {
            "interaction_id": interaction_id,
            "kind": kind,
            "outcome": outcome,
        },
    )


def tool_result_event(
    *,
    tool_name: str,
    tool_call_id: int,
    result: JsonObject,
) -> RuntimeEvent:
    """把持久化 Tool Result 映射为统一结果事件。"""
    payload = dict(result)
    outcome = payload.get("outcome")
    return RuntimeEvent(
        "result.available",
        {
            "kind": "tool_result",
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "outcome": outcome if isinstance(outcome, str) else "completed",
            "result": payload,
        },
    )
