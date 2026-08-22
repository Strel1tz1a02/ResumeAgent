"""Agent Runtime 的稳定命令、交互和 Graph 结果协议。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.ai_chat.types import JsonObject

RunStatus = Literal["running", "suspended", "completed", "failed", "cancelled"]


@dataclass(frozen=True)
class InteractionRequest:
    """Graph 在暂停前提交给 Runtime 的结构化外部交互请求。"""

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
        """返回可安全写入 LangGraph checkpoint 的 JSON 值。"""
        return {
            "type": "interaction.requested",
            "interaction_id": self.interaction_id,
            "kind": self.kind,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_value(cls, value: object) -> "InteractionRequest":
        """严格解析 LangGraph Interrupt 的不可信值。"""
        if not isinstance(value, dict):
            raise ValueError("Graph interrupt must contain an interaction object")
        if value.get("type") != "interaction.requested":
            raise ValueError("Graph interrupt has an unsupported type")
        if set(value) != {"type", "interaction_id", "kind", "payload"}:
            raise ValueError("Graph interrupt has unsupported fields")
        interaction_id = value.get("interaction_id")
        kind = value.get("kind")
        payload = value.get("payload")
        if isinstance(interaction_id, bool) or not isinstance(interaction_id, int):
            raise ValueError("Graph interrupt has no interaction identity")
        if not isinstance(kind, str) or not isinstance(payload, dict):
            raise ValueError("Graph interrupt has an invalid interaction payload")
        return cls(interaction_id=interaction_id, kind=kind, payload=dict(payload))


@dataclass(frozen=True)
class ResolveInteractionCommand:
    """客户端解决一个持久化 Interaction 的统一 Runtime 命令。"""

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
    """持久化 Resolution 完成后交给 Graph 的最小恢复命令。"""

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
        """返回不含客户端业务载荷的 checkpoint 恢复值。"""
        return {
            "type": "interaction_resolved",
            "run_id": self.run_id,
            "interaction_id": self.interaction_id,
        }

    @classmethod
    def from_value(cls, value: object) -> "GraphResumeCommand":
        """严格解析 Graph 中断节点收到的恢复值。"""
        if not isinstance(value, dict):
            raise ValueError("Graph resume value must be an object")
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
            raise ValueError("Graph resume value has invalid identities")
        return cls(run_id=run_id, interaction_id=interaction_id)


GraphOutcomeStatus = Literal["completed", "waiting"]


@dataclass(frozen=True)
class GraphOutcome:
    """Graph 一次执行流的唯一终止结果。"""

    status: GraphOutcomeStatus
    interaction: InteractionRequest | None = None

    def __post_init__(self) -> None:
        if self.status not in {"completed", "waiting"}:
            raise ValueError("Graph outcome has an unsupported status")
        if self.status == "waiting" and self.interaction is None:
            raise ValueError("waiting Graph outcome requires an interaction")
        if self.status == "completed" and self.interaction is not None:
            raise ValueError("completed Graph outcome cannot carry an interaction")

    @classmethod
    def completed(cls) -> "GraphOutcome":
        """构造正常完成结果。"""
        return cls(status="completed")

    @classmethod
    def waiting(cls, interaction: InteractionRequest) -> "GraphOutcome":
        """构造等待外部交互结果。"""
        return cls(status="waiting", interaction=interaction)


@dataclass(frozen=True)
class InteractionResolution:
    """Adapter 固化外部输入后返回给 Runtime 的恢复信息。"""

    resume: GraphResumeCommand
    replayed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.resume, GraphResumeCommand):
            raise TypeError("interaction resolution requires a Graph resume command")
        if not isinstance(self.replayed, bool):
            raise TypeError("interaction resolution replayed flag must be boolean")
