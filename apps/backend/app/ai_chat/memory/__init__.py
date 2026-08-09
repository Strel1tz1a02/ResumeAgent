"""有界会话记忆的单一公开入口。"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ai_chat.memory.service import MemoryContextService

__all__ = ["MemoryContextService"]


def __getattr__(name: str) -> object:
    """按需加载公开 Service，避免 Repository 与 Memory 子模块循环导入。"""
    if name == "MemoryContextService":
        from app.ai_chat.memory.service import MemoryContextService

        return MemoryContextService
    raise AttributeError(name)
