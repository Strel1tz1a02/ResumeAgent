"""Tool Call Interaction 载荷列迁移测试。"""

import sqlite3

from sqlalchemy import create_engine

from app.scripts.migrate_ai_chat_interaction_payload import migrate


def test_migration_renames_legacy_payload_without_data_loss(tmp_path) -> None:
    path = tmp_path / "legacy-interaction-payload.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE ai_chat_tool_calls "
            "(id INTEGER PRIMARY KEY, proposal_payload JSON)"
        )
        connection.execute(
            "INSERT INTO ai_chat_tool_calls (id,proposal_payload) "
            "VALUES (1,'{\"batch_id\":\"batch-1\"}')"
        )

    engine = create_engine(f"sqlite:///{path}")
    try:
        migrate(engine)
        migrate(engine)
        with engine.connect() as connection:
            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(ai_chat_tool_calls)"
                ).all()
            }
            payload = connection.exec_driver_sql(
                "SELECT interaction_payload FROM ai_chat_tool_calls WHERE id=1"
            ).scalar_one()
    finally:
        engine.dispose()

    assert "interaction_payload" in columns
    assert "proposal_payload" not in columns
    assert payload == '{"batch_id":"batch-1"}'
