"""Internal events consumed by future business-specific streaming APIs."""
from dataclasses import dataclass

from app.ai_chat.types import JsonObject


@dataclass(frozen=True)
class AiChatEvent:
    """One typed backend event with an opaque JSON payload."""

    event: str
    data: JsonObject
