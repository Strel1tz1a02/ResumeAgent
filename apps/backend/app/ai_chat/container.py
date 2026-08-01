"""Application-level composition root for the reusable AI Chat runtime."""

from pathlib import Path

from app import database as database_module
from app.ai_chat.adapters.base import BaseAdapter
from app.ai_chat.checkpoint import CheckpointLifecycle
from app.ai_chat.graph import GraphRunner
from app.ai_chat.model import AiChatModel
from app.ai_chat.registry import AdapterRegistry
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.service import AiChatService
from app.ai_chat.tools.lifecycle import ToolLifecycle
from app.config import settings

_registry = AdapterRegistry()
_checkpoints: CheckpointLifecycle | None = None
_repositories = RepositoryFactory()
_service: AiChatService | None = None


def _checkpoint_path() -> Path:
    """Use production data storage or the active isolated database directory."""
    active_database = database_module.db.db_path.resolve()
    configured_database = settings.sqlite_path.resolve()
    if active_database != configured_database:
        return active_database.parent / "ai_chat_checkpoints.db"
    return settings.ai_chat_checkpoint_path


def register_adapter(adapter: BaseAdapter) -> None:
    """Register one long-lived stateless business Adapter."""
    _registry.register(adapter)


async def start_ai_chat() -> None:
    """Initialize checkpoint persistence and compose the runtime once."""
    global _checkpoints, _service
    if _service is not None:
        return
    path = _checkpoint_path()
    if _checkpoints is None or _checkpoints.path != path:
        if _checkpoints is not None:
            await _checkpoints.close()
        _checkpoints = CheckpointLifecycle(path)
    saver = await _checkpoints.start()
    lifecycle = ToolLifecycle(_repositories)
    runner = GraphRunner(_registry, saver, AiChatModel(), lifecycle)
    _service = AiChatService(_registry, runner, lifecycle, _repositories)


def get_ai_chat_service() -> AiChatService:
    """Return the started internal service to a business Router or Service."""
    if _service is None:
        raise RuntimeError("AI Chat has not been started")
    return _service


async def close_ai_chat() -> None:
    """Close checkpoint resources without discarding Adapter registrations."""
    global _checkpoints, _service
    _service = None
    if _checkpoints is not None:
        await _checkpoints.close()
    _checkpoints = None


async def reset_ai_chat() -> None:
    """Remove all checkpoint state and restart the runtime."""
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
