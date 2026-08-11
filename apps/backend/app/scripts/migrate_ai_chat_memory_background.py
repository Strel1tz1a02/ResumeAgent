"""为后台 Memory 压缩增加 skipped 状态并回填 Outbox。"""

from __future__ import annotations

import json

from sqlalchemy import Engine, text

import app.background_jobs.models  # noqa: F401
from app.ai_chat.models import utcnow_iso
from app.models import Base

MIGRATION_NAME = "2026_08_10_ai_chat_memory_background"


def migrate(engine: Engine) -> None:
    """幂等升级 Memory 状态约束，并为未压缩终态 Run 建立事件。"""
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

        table_sql = connection.scalar(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='ai_chat_run_memories'"
            )
        ) or ""
        if table_sql and "'skipped'" not in table_sql:
            connection.exec_driver_sql(
                "CREATE TABLE ai_chat_run_memories_background_v2 ("
                "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,"
                "run_id INTEGER NOT NULL UNIQUE REFERENCES "
                "ai_chat_runs(id) ON DELETE CASCADE,"
                "status VARCHAR(16) NOT NULL,"
                "core JSON NOT NULL,other JSON NOT NULL,"
                "memory_token_count INTEGER NOT NULL,error_message TEXT,"
                "CONSTRAINT ck_ai_chat_run_memory_status CHECK "
                "(status IN ('pending','completed','skipped')))"
            )
            connection.exec_driver_sql(
                "INSERT INTO ai_chat_run_memories_background_v2 "
                "(id,run_id,status,core,other,memory_token_count,error_message) "
                "SELECT id,run_id,CASE WHEN status='completed' THEN 'completed' "
                "ELSE 'pending' END,core,other,memory_token_count,error_message "
                "FROM ai_chat_run_memories"
            )
            connection.exec_driver_sql("DROP TABLE ai_chat_run_memories")
            connection.exec_driver_sql(
                "ALTER TABLE ai_chat_run_memories_background_v2 "
                "RENAME TO ai_chat_run_memories"
            )
            connection.exec_driver_sql(
                "CREATE INDEX ix_ai_chat_run_memories_status "
                "ON ai_chat_run_memories (status)"
            )

        now = utcnow_iso()
        rows = connection.execute(
            text(
                "SELECT runs.id FROM ai_chat_runs AS runs "
                "LEFT JOIN ai_chat_run_memories AS memories "
                "ON memories.run_id = runs.id "
                "WHERE runs.status IN ('completed','failed') "
                "AND (memories.id IS NULL OR memories.status = 'pending') "
                "ORDER BY runs.id"
            )
        ).all()
        for (run_id,) in rows:
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO background_job_outbox "
                    "(topic,dedupe_key,payload,status,publish_attempts,"
                    "available_at,created_at,updated_at) "
                    "VALUES (:topic,:dedupe_key,:payload,'pending',0,:now,:now,:now)"
                ),
                {
                    "topic": "memory.compact",
                    "dedupe_key": f"memory.compact:{int(run_id)}",
                    "payload": json.dumps(
                        {"run_id": int(run_id)}, separators=(",", ":")
                    ),
                    "now": now,
                },
            )

        connection.execute(
            text(
                "INSERT INTO schema_migrations (name,applied_at) "
                "VALUES (:name,:now)"
            ),
            {"name": MIGRATION_NAME, "now": now},
        )
