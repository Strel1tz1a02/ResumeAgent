"""Lazy public entry points for the reusable backend AI Chat runtime."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ai_chat.adapters.base import BaseAdapter
    from app.ai_chat.service import AiChatService


def register_adapter(adapter: "BaseAdapter") -> None:
    """Register an Adapter without eagerly importing runtime dependencies."""
    from app.ai_chat.container import register_adapter as register

    register(adapter)


def get_ai_chat_service() -> "AiChatService":
    """Return the started service through the application container."""
    from app.ai_chat.container import get_ai_chat_service as get_service

    return get_service()


async def start_ai_chat() -> None:
    """Start the application-owned checkpoint lifecycle."""
    from app.ai_chat.container import start_ai_chat as start

    await start()


async def close_ai_chat() -> None:
    """Close the application-owned checkpoint lifecycle."""
    from app.ai_chat.container import close_ai_chat as close

    await close()


async def reset_ai_chat() -> None:
    """Reset the application-owned checkpoint lifecycle."""
    from app.ai_chat.container import reset_ai_chat as reset

    await reset()

__all__ = [
    "close_ai_chat",
    "get_ai_chat_service",
    "register_adapter",
    "reset_ai_chat",
    "start_ai_chat",
]
