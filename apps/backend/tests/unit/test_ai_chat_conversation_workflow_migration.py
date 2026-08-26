"""Conversation Workflow 持久化标识迁移测试。"""

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine

from app.scripts.migrate_ai_chat_conversation_workflow import (
    MIGRATION_NAME,
    migrate,
)


def test_migration_renames_adapter_column_and_builtin_names_idempotently(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-conversations.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE ai_chat_conversations ("
            "id INTEGER PRIMARY KEY, adapter VARCHAR NOT NULL, "
            "subject JSON NOT NULL, scope JSON NOT NULL)"
        )
        connection.execute(
            "CREATE INDEX ix_ai_chat_conversations_adapter "
            "ON ai_chat_conversations (adapter)"
        )
        connection.executemany(
            "INSERT INTO ai_chat_conversations VALUES (?, ?, ?, ?)",
            [
                (1, "ExperienceAdapter", '{"type":"experience","id":"7"}', "{}"),
                (2, "JDImportAdapter", '{"type":"jd_import","id":"new"}', "{}"),
                (3, "CustomWorkflow", '{"type":"custom","id":"1"}', "{}"),
            ],
        )

    engine = create_engine(f"sqlite:///{path}")
    try:
        migrate(engine)
        migrate(engine)
        with engine.connect() as connection:
            columns = {
                row["name"]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(ai_chat_conversations)"
                ).mappings()
            }
            rows = connection.exec_driver_sql(
                "SELECT id, workflow_name, subject "
                "FROM ai_chat_conversations ORDER BY id"
            ).all()
            indexes = {
                row["name"]
                for row in connection.exec_driver_sql(
                    "PRAGMA index_list(ai_chat_conversations)"
                ).mappings()
            }
            migration_count = connection.exec_driver_sql(
                "SELECT COUNT(*) FROM schema_migrations WHERE name = ?",
                (MIGRATION_NAME,),
            ).scalar_one()
    finally:
        engine.dispose()

    assert "workflow_name" in columns
    assert "adapter" not in columns
    assert rows == [
        (1, "ExperienceWorkflow", '{"type":"experience","id":"7"}'),
        (2, "JDImportWorkflow", '{"type":"jd_import","id":"new"}'),
        (3, "CustomWorkflow", '{"type":"custom","id":"1"}'),
    ]
    assert "ix_ai_chat_conversations_workflow_name" in indexes
    assert "ix_ai_chat_conversations_adapter" not in indexes
    assert migration_count == 1
