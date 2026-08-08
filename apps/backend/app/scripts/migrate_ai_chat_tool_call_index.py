"""为 AI Chat Tool Call 增加稳定的运行内索引。"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import Engine, text

from app.ai_chat.models import utcnow_iso
from app.models import Base

MIGRATION_NAME = "2026_08_07_ai_chat_tool_call_index"


def migrate(engine: Engine) -> None:
    """添加并回填 tool_call_index，再建立运行内唯一约束。"""
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
            "PRAGMA table_info(ai_chat_tool_calls)"
        ).mappings().all()
        if columns and "tool_call_index" not in {column["name"] for column in columns}:
            connection.exec_driver_sql(
                "ALTER TABLE ai_chat_tool_calls ADD COLUMN tool_call_index INTEGER"
            )

        rows = connection.exec_driver_sql(
            "SELECT id, run_id, tool_call_index "
            "FROM ai_chat_tool_calls ORDER BY run_id, id"
        ).all()
        used_indexes: dict[int, set[int]] = defaultdict(set)
        for _, run_id, tool_call_index in rows:
            if tool_call_index is not None:
                used_indexes[int(run_id)].add(int(tool_call_index))
        for tool_call_id, run_id, tool_call_index in rows:
            if tool_call_index is not None:
                continue
            used = used_indexes[int(run_id)]
            next_index = 0
            while next_index in used:
                next_index += 1
            connection.execute(
                text(
                    "UPDATE ai_chat_tool_calls SET tool_call_index = :tool_call_index "
                    "WHERE id = :tool_call_id"
                ),
                {
                    "tool_call_index": next_index,
                    "tool_call_id": int(tool_call_id),
                },
            )
            used.add(next_index)

        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_chat_tool_run_index "
            "ON ai_chat_tool_calls (run_id, tool_call_index)"
        )
        connection.execute(
            text("INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :now)"),
            {"name": MIGRATION_NAME, "now": utcnow_iso()},
        )
