"""将 AI Chat 会话绑定列从 target 统一改名为 scope。"""

from __future__ import annotations

from sqlalchemy import Engine, text

from app.ai_chat.models import utcnow_iso
from app.models import Base

MIGRATION_NAME = "2026_08_08_ai_chat_conversation_scope"


def migrate(engine: Engine) -> None:
    """幂等迁移旧会话表；新库只记录迁移状态。"""
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name VARCHAR(200) PRIMARY KEY, applied_at VARCHAR NOT NULL)"
        )
        if connection.scalar(
            text("SELECT 1 FROM schema_migrations WHERE name = :name"),
            {"name": MIGRATION_NAME},
        ):
            return

        columns = connection.exec_driver_sql(
            "PRAGMA table_info(ai_chat_conversations)"
        ).mappings().all()
        names = {column["name"] for column in columns}
        if "target" in names and "scope" not in names:
            connection.exec_driver_sql(
                "ALTER TABLE ai_chat_conversations RENAME COLUMN target TO scope"
            )

        connection.execute(
            text("INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :now)"),
            {"name": MIGRATION_NAME, "now": utcnow_iso()},
        )
