"""Conversation 服务及其横向 Workflow 能力的应用级组装入口。"""

from pathlib import Path
from typing import Any

from app import database as database_module
from app.ai_chat.checkpoint import CheckpointLifecycle
from app.ai_chat.memory import MemoryService
from app.ai_chat.persistence import (
    ConversationInteractionStore,
    SqlAlchemyToolCallStore,
)
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.services import ConversationService
from app.ai_chat.workflow import ConversationWorkflow
from app.config import settings
from app.llm import get_configured_max_tokens
from app.workflow_runtime import (
    CheckpointedWorkflowExecutor,
    ContextAssembler,
    InteractionCoordinator,
    ModelClient,
    WorkflowCatalog,
)
from app.workflow_runtime.tools import ToolLifecycleService

_catalog = WorkflowCatalog[ConversationWorkflow[Any]]()
_checkpoints: CheckpointLifecycle | None = None
_repositories = RepositoryFactory()
_conversations: ConversationService | None = None
_interactions: InteractionCoordinator | None = None


def _checkpoint_path() -> Path:
    """使用生产数据目录或当前隔离数据库所在目录。"""
    active_database = database_module.db.db_path.resolve()
    configured_database = settings.sqlite_path.resolve()
    if active_database != configured_database:
        return active_database.parent / "ai_chat_checkpoints.db"
    return settings.ai_chat_checkpoint_path


def register_workflow(workflow: ConversationWorkflow[Any]) -> None:
    """注册一个长期存活且无状态的对话 Workflow。"""
    _catalog.register(workflow)


async def start_ai_chat() -> None:
    """初始化检查点持久化，并完成一次运行时组装。"""
    global _checkpoints, _conversations, _interactions
    if _conversations is not None:
        return
    path = _checkpoint_path()
    if _checkpoints is None or _checkpoints.path != path:
        if _checkpoints is not None:
            await _checkpoints.close()
        _checkpoints = CheckpointLifecycle(path)
    checkpoint = await _checkpoints.start()
    tools = ToolLifecycleService(SqlAlchemyToolCallStore(database_module.db.session))
    memory = MemoryService()
    model = ModelClient(
        context=ContextAssembler(memory),
        max_tokens=get_configured_max_tokens(),
    )
    workflows = CheckpointedWorkflowExecutor(
        _catalog.get,
        checkpoint,
        model,
        tools,
        thread_namespace="ai-chat",
    )
    _conversations = ConversationService(
        resolve_workflow=_catalog.get,
        repositories=_repositories,
        session_factory=database_module.db.session,
        workflows=workflows,
        tools=tools,
    )
    _interactions = InteractionCoordinator(
        resolve_workflow=_catalog.get,
        workflows=workflows,
        tools=tools,
        store=ConversationInteractionStore(
            session_factory=database_module.db.session,
            repositories=_repositories,
        ),
    )


def get_conversation_service() -> ConversationService:
    """返回完整的 Conversation 应用服务。"""
    if _conversations is None:
        raise RuntimeError("AI Chat has not been started")
    return _conversations


def get_interaction_coordinator() -> InteractionCoordinator:
    """返回由当前应用存储支持的通用 Interaction 能力。"""
    if _interactions is None:
        raise RuntimeError("AI Chat has not been started")
    return _interactions


async def close_ai_chat() -> None:
    """关闭检查点资源，但保留 Workflow 注册信息。"""
    global _checkpoints, _conversations, _interactions
    _conversations = None
    _interactions = None
    if _checkpoints is not None:
        await _checkpoints.close()
    _checkpoints = None


async def reset_ai_chat() -> None:
    """清除全部检查点状态并重新启动运行时。"""
    global _checkpoints, _conversations, _interactions
    _conversations = None
    _interactions = None
    desired_path = _checkpoint_path()
    if _checkpoints is not None and _checkpoints.path != desired_path:
        await _checkpoints.close()
        _checkpoints = None
    if _checkpoints is None:
        _checkpoints = CheckpointLifecycle(desired_path)
    await _checkpoints.reset()
    await start_ai_chat()
