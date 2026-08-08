"""将旧经历会话范围规范化为单一 field。"""

from __future__ import annotations

import json

from sqlalchemy import Engine, text

from app.ai_chat.models import utcnow_iso
from app.models import Base

MIGRATION_NAME = "2026_08_08_experience_chat_scope_field"


def migrate(engine: Engine) -> None:
    """迁移 ExperienceAdapter 已持久化的旧 scope JSON。"""
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
        if {"id", "adapter", "scope"} <= columns:
            rows = connection.execute(
                text(
                    "SELECT id, scope FROM ai_chat_conversations "
                    "WHERE adapter = 'ExperienceAdapter'"
                )
            ).all()
            for conversation_id, raw_scope in rows:
                value = raw_scope
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError:
                        continue
                if not isinstance(value, dict) or not isinstance(value.get("key"), str):
                    continue
                connection.execute(
                    text(
                        "UPDATE ai_chat_conversations SET scope = :scope "
                        "WHERE id = :conversation_id"
                    ),
                    {
                        "scope": json.dumps(
                            {"field": value["key"]},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "conversation_id": int(conversation_id),
                    },
                )

        connection.execute(
            text("INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :now)"),
            {"name": MIGRATION_NAME, "now": utcnow_iso()},
        )
