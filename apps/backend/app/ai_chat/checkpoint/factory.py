"""创建和关闭专用异步 SQLite 检查点存储。"""

import os
from pathlib import Path
from typing import Any

# LangGraph 在导入序列化模块时读取此设置。
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
"""
保证：
    全局只打开一次；
    退出应用时正确释放文件句柄；
    重置前先关闭连接，再删除 .db/-wal/-shm 文件。
"""

class CheckpointLifecycle:
    """在应用生命周期内持有 AsyncSqliteSaver 上下文。"""

    def __init__(self, path: Path) -> None:
        """配置专用检查点数据库路径。"""
        self.path = path
        self.saver: AsyncSqliteSaver | None = None
        self._context: Any = None

    async def start(self) -> AsyncSqliteSaver:
        """仅打开一次存储器，并启用严格的 MessagePack 解码。"""
        if self.saver is not None:
            return self.saver
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._context = AsyncSqliteSaver.from_conn_string(str(self.path))
        self.saver = await self._context.__aenter__()
        await self.saver.setup()
        return self.saver

    async def close(self) -> None:
        """关闭存储器并释放 SQLite 文件句柄。"""
        if self._context is not None:
            await self._context.__aexit__(None, None, None)
        self._context = None
        self.saver = None

    async def reset(self) -> None:
        """关闭并移除检查点数据库及其附属文件。"""
        await self.close()
        for candidate in (
            self.path,
            self.path.with_name(f"{self.path.name}-wal"),
            self.path.with_name(f"{self.path.name}-shm"),
        ):
            candidate.unlink(missing_ok=True)
