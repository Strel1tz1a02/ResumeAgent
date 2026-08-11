"""SQLAlchemy 数据层的 SQLite 引擎与会话组装。

每个 ``Database`` 实例都通过这些工厂创建并持有自己的引擎：文档表使用异步
引擎，同步 LLM 热路径读取加密 ``api_keys`` 表时使用同步引擎。集中组装逻辑后，
测试可以基于临时数据库文件创建完全隔离的引擎。
"""

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.models import Base

__all__ = ["Base", "make_async_engine", "make_sync_engine", "init_models_sync"]


def _apply_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """为每个 SQLite 连接设置 PRAGMA。

    异步文档表引擎和同步 ``api_keys`` 引擎指向同一文件，WAL 用于改善并发读写；
    ``busy_timeout`` 用于等待短暂的锁竞争；``foreign_keys`` 用于启用 SQLite
    默认关闭的外键完整性检查。
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def _url(path: Path, *, driver: str) -> str:
    """构造 SQLite URL；绝对路径会生成规范要求的四个斜杠。"""
    return f"sqlite+{driver}:///{path}" if driver else f"sqlite:///{path}"


def make_async_engine(path: Path) -> AsyncEngine:
    """为文档表创建基于 ``aiosqlite`` 的异步引擎。"""
    engine = create_async_engine(_url(path, driver="aiosqlite"), future=True)
    event.listen(engine.sync_engine, "connect", _apply_sqlite_pragmas)
    return engine


def make_sync_engine(path: Path) -> Engine:
    """为加密的 ``api_keys`` 表创建同步引擎。

    密钥读取链路（``get_llm_config`` → ``load_config_file`` →
    ``resolve_api_key``）是同步的，因此使用同步引擎可以避免让 ``llm.py``
    额外传递异步调用；它与异步引擎指向同一个数据库文件。
    """
    engine = create_engine(_url(path, driver=""), future=True)
    event.listen(engine, "connect", _apply_sqlite_pragmas)
    return engine


def init_models_sync(engine: Engine) -> None:
    """使用同步引擎连接幂等创建全部数据表。"""
    # 模块 ORM 共享同一个声明式 Base；create_all 前必须显式注册。
    import app.experience.models  # noqa: F401
    import app.ai_chat.models  # noqa: F401
    import app.background_jobs.models  # noqa: F401

    from app.scripts.migrate_experience_field_states import migrate
    from app.scripts.migrate_experience_evidence_items import (
        migrate as migrate_experience_evidence_items,
    )
    from app.scripts.migrate_experience_revisions import (
        migrate as migrate_experience_revisions,
    )
    from app.scripts.migrate_unified_experience_revision_units import (
        migrate as migrate_unified_experience_revision_units,
    )
    from app.scripts.migrate_ai_chat_tool_call_index import (
        migrate as migrate_ai_chat_tool_call_index,
    )
    from app.scripts.migrate_ai_chat_tool_call_state import (
        migrate as migrate_ai_chat_tool_call_state,
    )
    from app.scripts.migrate_ai_chat_conversation_scope import (
        migrate as migrate_ai_chat_conversation_scope,
    )
    from app.scripts.migrate_experience_chat_scope_field import (
        migrate as migrate_experience_chat_scope_field,
    )
    from app.scripts.migrate_ai_chat_memory_background import (
        migrate as migrate_ai_chat_memory_background,
    )

    migrate(engine)
    migrate_experience_evidence_items(engine)
    migrate_experience_revisions(engine)
    migrate_unified_experience_revision_units(engine)
    migrate_ai_chat_tool_call_index(engine)
    migrate_ai_chat_tool_call_state(engine)
    migrate_ai_chat_conversation_scope(engine)
    migrate_experience_chat_scope_field(engine)
    migrate_ai_chat_memory_background(engine)

    # ``create_all`` 不会修改既有 SQLite 表，因此增量迁移必须保持幂等，
    # 使旧版数据库仍能安全加载简历。
    with engine.begin() as conn:
        columns = conn.exec_driver_sql("PRAGMA table_info(resumes)").mappings().all()
        if columns and "interview_prep" not in {column["name"] for column in columns}:
            conn.exec_driver_sql("ALTER TABLE resumes ADD COLUMN interview_prep TEXT")
