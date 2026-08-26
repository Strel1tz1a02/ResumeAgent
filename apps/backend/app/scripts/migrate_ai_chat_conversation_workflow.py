"""将 Conversation 的持久化执行标识从 Adapter 统一为 Workflow。"""

from __future__ import annotations

from sqlalchemy import Engine, text

from app.ai_chat.models import utcnow_iso
from app.models import Base

MIGRATION_NAME = "2026_08_25_ai_chat_conversation_workflow"

_LEGACY_NAMES = {
    "ExperienceAdapter": "ExperienceWorkflow",
    "JDImportAdapter": "JDImportWorkflow",
}


def migrate(engine: Engine) -> None:
    """幂等重命名列、索引及内置 Workflow 名称。"""
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

        columns = {
            column["name"]
            for column in connection.exec_driver_sql(
                "PRAGMA table_info(ai_chat_conversations)"
            ).mappings()
        }
        if "adapter" in columns and "workflow_name" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE ai_chat_conversations "
                "RENAME COLUMN adapter TO workflow_name"
            )
            columns.remove("adapter")
            columns.add("workflow_name")

        if "workflow_name" in columns:
            for old_name, new_name in _LEGACY_NAMES.items():
                connection.execute(
                    text(
                        "UPDATE ai_chat_conversations "
                        "SET workflow_name = :new_name "
                        "WHERE workflow_name = :old_name"
                    ),
                    {"old_name": old_name, "new_name": new_name},
                )
            connection.exec_driver_sql(
                "DROP INDEX IF EXISTS ix_ai_chat_conversations_adapter"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS "
                "ix_ai_chat_conversations_workflow_name "
                "ON ai_chat_conversations (workflow_name)"
            )

        connection.execute(
            text("INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :now)"),
            {"name": MIGRATION_NAME, "now": utcnow_iso()},
        )
