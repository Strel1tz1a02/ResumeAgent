"""Tests for durable AI Chat Tool Call persistence."""

import sqlite3

from sqlalchemy import create_engine

from app.scripts.migrate_ai_chat_tool_call_state import (
    migrate as migrate_ai_chat_tool_call_state,
)


def test_tool_call_state_migration_backfills_validated(tmp_path) -> None:
    path = tmp_path / "legacy-tool-state.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "CREATE TABLE ai_chat_tool_calls ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL, "
            "run_id INTEGER NOT NULL, tool_call_index INTEGER NOT NULL, "
            "provider_tool_call_id VARCHAR(200), tool_name VARCHAR(160) NOT NULL, "
            "arguments JSON NOT NULL, proposal_payload JSON, guard_payload JSON, "
            "status VARCHAR(24) NOT NULL, decision VARCHAR(16), tool_result JSON, "
            "delivery_status VARCHAR(16), client_resolution_id VARCHAR(160), "
            "resolved_at VARCHAR, created_at VARCHAR NOT NULL, updated_at VARCHAR NOT NULL);"
        )
        rows = [
            (1, "received", '{"proposal":1}', '{"guard":1}', None),
            (2, "received", None, None, None),
            (3, "awaiting_approval", '{"proposal":3}', '{"guard":3}', None),
            (4, "resolved", None, None, '{"outcome":"done"}'),
        ]
        connection.executemany(
            "INSERT INTO ai_chat_tool_calls "
            "(id,conversation_id,run_id,tool_call_index,tool_name,arguments,"
            "proposal_payload,guard_payload,status,tool_result,created_at,updated_at) "
            "VALUES (?,1,1,?, 'demo', '{}', ?, ?, ?, ?, 'now', 'now')",
            [
                (row_id, row_id - 1, proposal, guard, status, result)
                for row_id, status, proposal, guard, result in rows
            ],
        )
    engine = create_engine(f"sqlite:///{path}")
    try:
        migrate_ai_chat_tool_call_state(engine)
        migrate_ai_chat_tool_call_state(engine)
        with engine.connect() as connection:
            values = connection.exec_driver_sql(
                "SELECT id,status FROM ai_chat_tool_calls ORDER BY id"
            ).all()
            table_sql = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='ai_chat_tool_calls'"
            ).scalar_one()
        assert values == [
            (1, "validated"),
            (2, "received"),
            (3, "awaiting_approval"),
            (4, "resolved"),
        ]
        assert "'approved'" in table_sql
        assert "status != 'resolved' OR tool_result IS NOT NULL" in table_sql
    finally:
        engine.dispose()
