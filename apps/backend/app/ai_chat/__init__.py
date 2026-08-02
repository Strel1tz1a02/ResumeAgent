"""可复用后端 AI 对话运行时的延迟加载公开入口。"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ai_chat.adapters.base import BaseAdapter
    from app.ai_chat.service import AiChatService


def register_adapter(adapter: "BaseAdapter") -> None:
    """注册适配器，同时避免提前导入运行时依赖。"""
    from app.ai_chat.container import register_adapter as register

    register(adapter)


def get_ai_chat_service() -> "AiChatService":
    """通过应用容器返回已启动的服务。"""
    from app.ai_chat.container import get_ai_chat_service as get_service

    return get_service()


async def start_ai_chat() -> None:
    """启动由应用持有的检查点生命周期。"""
    from app.ai_chat.container import start_ai_chat as start

    await start()


async def close_ai_chat() -> None:
    """关闭由应用持有的检查点生命周期。"""
    from app.ai_chat.container import close_ai_chat as close

    await close()


async def reset_ai_chat() -> None:
    """重置由应用持有的检查点生命周期。"""
    from app.ai_chat.container import reset_ai_chat as reset

    await reset()

__all__ = [
    "close_ai_chat",
    "get_ai_chat_service",
    "register_adapter",
    "reset_ai_chat",
    "start_ai_chat",
]
