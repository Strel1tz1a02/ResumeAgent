"""后端 Conversation 能力的延迟加载公开入口。"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ai_chat.services import ConversationService
    from app.ai_chat.workflow import ConversationWorkflow
    from app.workflow_runtime import InteractionCoordinator


def register_workflow(workflow: "ConversationWorkflow") -> None:
    """注册对话 Workflow，同时避免提前导入运行时依赖。"""
    from app.ai_chat.container import register_workflow as register

    register(workflow)


def get_conversation_service() -> "ConversationService":
    """通过应用容器返回 Conversation 服务。"""
    from app.ai_chat.container import get_conversation_service as get_service

    return get_service()


def get_interaction_coordinator() -> "InteractionCoordinator":
    """返回应用组装的通用 Interaction 协调器。"""
    from app.ai_chat.container import get_interaction_coordinator as get_coordinator

    return get_coordinator()


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
    "get_conversation_service",
    "get_interaction_coordinator",
    "register_workflow",
    "reset_ai_chat",
    "start_ai_chat",
]
