"""Create and close the dedicated asynchronous SQLite checkpointer."""

import os
from pathlib import Path
from typing import Any

# LangGraph reads this setting while importing its serializer module.
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


class CheckpointLifecycle:
    """Own the AsyncSqliteSaver context for the application lifespan."""

    def __init__(self, path: Path) -> None:
        """Configure the dedicated checkpoint database path."""
        self.path = path
        self.saver: AsyncSqliteSaver | None = None
        self._context: Any = None

    async def start(self) -> AsyncSqliteSaver:
        """Open the saver once and enable strict MessagePack decoding."""
        if self.saver is not None:
            return self.saver
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._context = AsyncSqliteSaver.from_conn_string(str(self.path))
        self.saver = await self._context.__aenter__()
        await self.saver.setup()
        return self.saver

    async def close(self) -> None:
        """Close the saver and release the SQLite file handle."""
        if self._context is not None:
            await self._context.__aexit__(None, None, None)
        self._context = None
        self.saver = None

    async def reset(self) -> None:
        """Close and remove the checkpoint database and its sidecar files."""
        await self.close()
        for candidate in (
            self.path,
            self.path.with_name(f"{self.path.name}-wal"),
            self.path.with_name(f"{self.path.name}-shm"),
        ):
            candidate.unlink(missing_ok=True)
