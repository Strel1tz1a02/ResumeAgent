"""Construct transaction-scoped AI Chat repositories."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.repositories.conversation_repository import ConversationRepository
from app.ai_chat.repositories.message_repository import MessageRepository
from app.ai_chat.repositories.run_repository import RunRepository
from app.ai_chat.repositories.tool_call_repository import ToolCallRepository


@dataclass(frozen=True)
class AiChatRepositories:
    """Repositories sharing one caller-owned SQLAlchemy session."""

    conversations: ConversationRepository
    messages: MessageRepository
    runs: RunRepository
    tool_calls: ToolCallRepository


class RepositoryFactory:
    """Create a repository bundle without taking over transaction control."""

    def create(self, session: AsyncSession) -> AiChatRepositories:
        """Bind all repositories to the supplied session."""
        return AiChatRepositories(
            conversations=ConversationRepository(session),
            messages=MessageRepository(session),
            runs=RunRepository(session),
            tool_calls=ToolCallRepository(session),
        )
