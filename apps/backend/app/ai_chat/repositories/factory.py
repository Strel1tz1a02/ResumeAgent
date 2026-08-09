"""构造事务范围内的 AI 对话仓储。"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_chat.repositories.conversation_repository import ConversationRepository
from app.ai_chat.repositories.message_repository import MessageRepository
from app.ai_chat.repositories.memory_repository import MemoryRepository
from app.ai_chat.repositories.run_repository import RunRepository
from app.ai_chat.repositories.tool_call_repository import ToolCallRepository


@dataclass(frozen=True)
class AiChatRepositories:
    """共享同一调用方 SQLAlchemy 会话的仓储集合。"""

    conversations: ConversationRepository
    messages: MessageRepository
    runs: RunRepository
    tool_calls: ToolCallRepository
    memory: MemoryRepository | None = None


class RepositoryFactory:
    """创建仓储集合，但不接管事务控制。"""

    def create(self, session: AsyncSession) -> AiChatRepositories:
        """将全部仓储绑定到传入的会话。"""
        return AiChatRepositories(
            conversations=ConversationRepository(session),
            messages=MessageRepository(session),
            memory=MemoryRepository(session),
            runs=RunRepository(session),
            tool_calls=ToolCallRepository(session),
        )
