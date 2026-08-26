"""所有 Workflow 调用方共享的事件信封。"""

import re
from dataclasses import dataclass

from app.workflow_runtime.errors import InteractionStateError
from app.workflow_runtime.protocol import GraphOutcome
from app.workflow_runtime.types import JsonObject

_EVENT_TYPE = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*")


@dataclass(frozen=True)
class RuntimeEvent:
    type: str
    payload: JsonObject
    run_id: int | str | None = None
    sequence: int | None = None

    def __post_init__(self) -> None:
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
                raise ValueError(
                    "Runtime event run id must be a positive integer or string"
                )
        if self.sequence is not None and (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence <= 0
        ):
            raise ValueError("Runtime event sequence must be a positive integer")

    def bind(self, *, run_id: int | str, sequence: int | None = None) -> "RuntimeEvent":
        return RuntimeEvent(
            type=self.type,
            payload=dict(self.payload),
            run_id=run_id,
            sequence=sequence,
        )

    def envelope(self) -> JsonObject:
        value: JsonObject = {"type": self.type, "payload": dict(self.payload)}
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
    return RuntimeEvent(event_type, payload or {}, run_id=run_id)


def output_delta_event(text: str) -> RuntimeEvent:
    return RuntimeEvent("output.delta", {"text": text})


def interaction_requested_event(
    *, interaction_id: int, kind: str, payload: JsonObject
) -> RuntimeEvent:
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
    return RuntimeEvent(
        "interaction.resolved",
        {
            "interaction_id": interaction_id,
            "kind": kind,
            "outcome": outcome,
        },
    )


def tool_result_event(
    *, tool_name: str, tool_call_id: int, result: JsonObject
) -> RuntimeEvent:
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


def outcome_events(
    *,
    run_id: int | str,
    outcome: GraphOutcome,
    completed_payload: JsonObject | None = None,
) -> tuple[RuntimeEvent, ...]:
    """把 Graph Outcome 映射成统一的 Run/Interaction 事件。"""
    if outcome.status == "completed":
        return (run_event("run.completed", run_id, completed_payload),)
    interaction = outcome.interaction
    if interaction is None:
        raise InteractionStateError("Waiting Graph has no interaction")
    return (
        run_event(
            "run.suspended",
            run_id,
            {
                "interaction_id": interaction.interaction_id,
                "kind": interaction.kind,
            },
        ),
        interaction_requested_event(
            interaction_id=interaction.interaction_id,
            kind=interaction.kind,
            payload=interaction.payload,
        ).bind(run_id=run_id),
    )
