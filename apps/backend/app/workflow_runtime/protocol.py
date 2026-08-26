"""Workflow 执行、交互和终止结果协议。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.workflow_runtime.types import JsonObject

RunStatus = Literal["running", "suspended", "completed", "failed", "cancelled"]


@dataclass(frozen=True)
class InteractionRequest:
    interaction_id: int
    kind: str
    payload: JsonObject

    def __post_init__(self) -> None:
        if (
            isinstance(self.interaction_id, bool)
            or not isinstance(self.interaction_id, int)
            or self.interaction_id <= 0
        ):
            raise ValueError("interaction id must be a positive integer")
        if (
            not isinstance(self.kind, str)
            or not self.kind.strip()
            or len(self.kind) > 80
        ):
            raise ValueError("interaction kind must be non-empty and at most 80 characters")
        if not isinstance(self.payload, dict):
            raise TypeError("interaction payload must be an object")

    def interrupt_value(self) -> JsonObject:
        return {
            "type": "interaction.requested",
            "interaction_id": self.interaction_id,
            "kind": self.kind,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_value(cls, value: object) -> InteractionRequest:
        if not isinstance(value, dict):
            raise TypeError("Graph interrupt must contain an interaction object")
        if value.get("type") != "interaction.requested":
            raise ValueError("Graph interrupt has an unsupported type")
        if set(value) != {"type", "interaction_id", "kind", "payload"}:
            raise ValueError("Graph interrupt has unsupported fields")
        interaction_id = value.get("interaction_id")
        kind = value.get("kind")
        payload = value.get("payload")
        if isinstance(interaction_id, bool) or not isinstance(interaction_id, int):
            raise TypeError("Graph interrupt has no interaction identity")
        if not isinstance(kind, str) or not isinstance(payload, dict):
            raise TypeError("Graph interrupt has an invalid interaction payload")
        return cls(interaction_id=interaction_id, kind=kind, payload=dict(payload))


@dataclass(frozen=True)
class ResolveInteractionCommand:
    run_id: int
    interaction_id: int
    kind: str
    client_resolution_id: str
    payload: JsonObject

    def __post_init__(self) -> None:
        if (
            isinstance(self.run_id, bool)
            or not isinstance(self.run_id, int)
            or self.run_id <= 0
        ):
            raise ValueError("run id must be a positive integer")
        if (
            isinstance(self.interaction_id, bool)
            or not isinstance(self.interaction_id, int)
            or self.interaction_id <= 0
        ):
            raise ValueError("interaction id must be a positive integer")
        if (
            not isinstance(self.kind, str)
            or not self.kind.strip()
            or len(self.kind) > 80
        ):
            raise ValueError("interaction kind must be non-empty and at most 80 characters")
        if (
            not isinstance(self.client_resolution_id, str)
            or not self.client_resolution_id.strip()
            or len(self.client_resolution_id) > 200
        ):
            raise ValueError(
                "client resolution id must be non-empty and at most 200 characters"
            )
        if not isinstance(self.payload, dict):
            raise TypeError("interaction resolution payload must be an object")


@dataclass(frozen=True)
class GraphResumeCommand:
    run_id: int
    interaction_id: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.run_id, bool)
            or not isinstance(self.run_id, int)
            or self.run_id <= 0
        ):
            raise ValueError("run id must be a positive integer")
        if (
            isinstance(self.interaction_id, bool)
            or not isinstance(self.interaction_id, int)
            or self.interaction_id <= 0
        ):
            raise ValueError("interaction id must be a positive integer")

    def resume_value(self) -> JsonObject:
        return {
            "type": "interaction_resolved",
            "run_id": self.run_id,
            "interaction_id": self.interaction_id,
        }

    @classmethod
    def from_value(cls, value: object) -> GraphResumeCommand:
        if not isinstance(value, dict):
            raise TypeError("Graph resume value must be an object")
        if set(value) != {"type", "run_id", "interaction_id"}:
            raise ValueError("Graph resume value has unsupported fields")
        if value.get("type") != "interaction_resolved":
            raise ValueError("Graph resume value has an unsupported type")
        run_id = value.get("run_id")
        interaction_id = value.get("interaction_id")
        if (
            isinstance(run_id, bool)
            or not isinstance(run_id, int)
            or isinstance(interaction_id, bool)
            or not isinstance(interaction_id, int)
        ):
            raise TypeError("Graph resume value has invalid identities")
        return cls(run_id=run_id, interaction_id=interaction_id)


GraphOutcomeStatus = Literal["completed", "waiting"]


@dataclass(frozen=True)
class GraphOutcome:
    status: GraphOutcomeStatus
    interaction: InteractionRequest | None = None

    def __post_init__(self) -> None:
        if self.status not in {"completed", "waiting"}:
            raise ValueError("Graph outcome has an unsupported status")
        if self.status == "waiting" and self.interaction is None:
            raise ValueError("waiting Graph outcome requires an interaction")
        if self.status != "waiting" and self.interaction is not None:
            raise ValueError(f"{self.status} Graph outcome cannot carry an interaction")

    @classmethod
    def completed(cls) -> GraphOutcome:
        return cls(status="completed")

    @classmethod
    def waiting(cls, interaction: InteractionRequest) -> GraphOutcome:
        return cls(status="waiting", interaction=interaction)


@dataclass(frozen=True)
class InteractionResolution:
    resume: GraphResumeCommand
    replayed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.resume, GraphResumeCommand):
            raise TypeError("interaction resolution requires a Graph resume command")
        if not isinstance(self.replayed, bool):
            raise TypeError("interaction resolution replayed flag must be boolean")
