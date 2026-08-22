"""可复用 AI 对话运行时的应用级组装入口。"""

from pathlib import Path

from app import database as database_module
from app.ai_chat.adapters import AdapterRegistry, BaseAdapter
from app.ai_chat.checkpoint import CheckpointLifecycle
from app.ai_chat.context import ContextAssembler
from app.ai_chat.graph.runner import GraphRunner
from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.memory import MemoryService
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.services import AiChatService, ToolService
from app.ai_chat.streaming import AiChatModel
from app.ai_chat.tools.store import ToolCallStore
from app.config import settings
from app.llm import get_configured_max_tokens

_registry = AdapterRegistry()
_checkpoints: CheckpointLifecycle | None = None
_repositories = RepositoryFactory()
_service: AiChatService | None = None


def _checkpoint_path() -> Path:
    """使用生产数据目录或当前隔离数据库所在目录。"""
    active_database = database_module.db.db_path.resolve()
    configured_database = settings.sqlite_path.resolve()
    if active_database != configured_database:
        return active_database.parent / "ai_chat_checkpoints.db"
    return settings.ai_chat_checkpoint_path


def register_adapter(adapter: BaseAdapter) -> None:
    """注册一个长期存活且无状态的业务适配器。"""
    _registry.register(adapter)


async def start_ai_chat() -> None:
    """初始化检查点持久化，并完成一次运行时组装。"""
    global _checkpoints, _service
    if _service is not None:
        return
    path = _checkpoint_path()
    if _checkpoints is None or _checkpoints.path != path:
        if _checkpoints is not None:
            await _checkpoints.close()
        _checkpoints = CheckpointLifecycle(path)
    checkpoint = await _checkpoints.start()
    tools = ToolService(ToolCallStore(database_module.db.session, _repositories))
    memory = MemoryService()
    runtime = AiChatRuntime(
        AiChatModel(),
        tools,
        ContextAssembler(memory),
        max_tokens=get_configured_max_tokens(),
    )
    runner = GraphRunner(_registry, checkpoint, runtime)
    _service = AiChatService(_registry, runner, _repositories)


def get_ai_chat_service() -> AiChatService:
    """向业务路由或服务返回已启动的内部服务。"""
    if _service is None:
        raise RuntimeError("AI Chat has not been started")
    return _service


async def close_ai_chat() -> None:
    """关闭检查点资源，但保留适配器注册信息。"""
    global _checkpoints, _service
    _service = None
    if _checkpoints is not None:
        await _checkpoints.close()
    _checkpoints = None


async def reset_ai_chat() -> None:
    """清除全部检查点状态并重新启动运行时。"""
    global _checkpoints, _service
    _service = None
    desired_path = _checkpoint_path()
    if _checkpoints is not None and _checkpoints.path != desired_path:
        await _checkpoints.close()
        _checkpoints = None
    if _checkpoints is None:
        _checkpoints = CheckpointLifecycle(desired_path)
    await _checkpoints.reset()
    await start_ai_chat()
