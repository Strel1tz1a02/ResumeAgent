"""Abstract boundary implemented by every business AI Adapter."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import TYPE_CHECKING

from langgraph.graph import StateGraph

from app.ai_chat.types import (
    AdapterInput,
    JsonValue,
    SubjectRef,
    TargetRef,
    ValidatedBinding,
)

if TYPE_CHECKING:
    from app.ai_chat.runtime import AiChatRuntime
    from app.ai_chat.tools.handler import ToolHandler


class BaseAdapter(ABC):
    """Translate common chat inputs into one stateless business Graph."""

    @classmethod
    def adapter_name(cls) -> str:
        """Return the stable name persisted in conversations and the Registry."""
        return cls.__name__

    @abstractmethod
    async def validate_binding(
        self, subject: SubjectRef, target: TargetRef
    ) -> ValidatedBinding:
        """Validate and normalize a business binding before it is persisted."""

    @abstractmethod
    async def parse_input(self, value: AdapterInput) -> dict[str, JsonValue]:
        """Create serializable business State fields for one Graph invocation."""

    @abstractmethod
    def build_graph(self, runtime: "AiChatRuntime") -> StateGraph:
        """Return the uncompiled business Graph definition."""

    @abstractmethod
    def get_tool_handlers(self) -> Mapping[str, "ToolHandler"]:
        """Return the Tool Handlers available to this business Graph."""
