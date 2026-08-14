"""为持久化 AI Chat Tool Call 增加 awaiting_input 状态。"""

from sqlalchemy import Engine, text

from app.ai_chat.models import utcnow_iso
from app.models import Base

MIGRATION_NAME = "2026_08_14_ai_chat_tool_input_state"


def migrate(engine: Engine) -> None:
    """幂等重建 SQLite Tool Call 状态约束。"""
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        transaction = connection.begin()
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name VARCHAR(200) PRIMARY KEY, applied_at VARCHAR NOT NULL)"
        )
        if connection.scalar(
            text("SELECT 1 FROM schema_migrations WHERE name = :name"),
            {"name": MIGRATION_NAME},
        ):
            transaction.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
            return
        table_sql = connection.scalar(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='ai_chat_tool_calls'"
            )
        ) or ""
        if "'awaiting_input'" not in table_sql:
            connection.exec_driver_sql(
                "CREATE TABLE ai_chat_tool_calls_input_v3 ("
                "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,"
                "conversation_id INTEGER NOT NULL REFERENCES "
                "ai_chat_conversations(id) ON DELETE CASCADE,"
                "run_id INTEGER NOT NULL REFERENCES ai_chat_runs(id) ON DELETE CASCADE,"
                "tool_call_index INTEGER NOT NULL,provider_tool_call_id VARCHAR(200),"
                "tool_name VARCHAR(160) NOT NULL,arguments JSON NOT NULL,"
                "proposal_payload JSON,guard_payload JSON,status VARCHAR(24) NOT NULL,"
                "decision VARCHAR(16),tool_result JSON,delivery_status VARCHAR(16),"
                "client_resolution_id VARCHAR(160),resolved_at VARCHAR,"
                "created_at VARCHAR NOT NULL,updated_at VARCHAR NOT NULL,"
                "CONSTRAINT ck_ai_chat_tool_status CHECK (status IN "
                "('received','validated','awaiting_approval','awaiting_input',"
                "'approved','executing','resolved'))," 
                "CONSTRAINT ck_ai_chat_tool_decision CHECK "
                "(decision IS NULL OR decision IN ('approve','reject'))," 
                "CONSTRAINT ck_ai_chat_tool_delivery CHECK "
                "(delivery_status IS NULL OR delivery_status IN ('pending','consumed'))," 
                "CONSTRAINT ck_ai_chat_tool_result CHECK "
                "(status != 'resolved' OR tool_result IS NOT NULL),"
                "CONSTRAINT uq_ai_chat_tool_resolution_id UNIQUE "
                "(conversation_id,client_resolution_id))"
            )
            connection.exec_driver_sql(
                "INSERT INTO ai_chat_tool_calls_input_v3 "
                "(id,conversation_id,run_id,tool_call_index,provider_tool_call_id,"
                "tool_name,arguments,proposal_payload,guard_payload,status,decision,"
                "tool_result,delivery_status,client_resolution_id,resolved_at,"
                "created_at,updated_at) SELECT id,conversation_id,run_id,"
                "tool_call_index,provider_tool_call_id,tool_name,arguments,"
                "proposal_payload,guard_payload,status,decision,tool_result,"
                "delivery_status,client_resolution_id,resolved_at,created_at,updated_at "
                "FROM ai_chat_tool_calls"
            )
            connection.exec_driver_sql("DROP TABLE ai_chat_tool_calls")
            connection.exec_driver_sql(
                "ALTER TABLE ai_chat_tool_calls_input_v3 RENAME TO ai_chat_tool_calls"
            )
            for sql in (
                (
                    "CREATE INDEX ix_ai_chat_tool_calls_conversation_id "
                    "ON ai_chat_tool_calls (conversation_id)"
                ),
                "CREATE INDEX ix_ai_chat_tool_calls_run_id ON ai_chat_tool_calls (run_id)",
                "CREATE INDEX ix_ai_chat_tool_calls_status ON ai_chat_tool_calls (status)",
                (
                    "CREATE INDEX ix_ai_chat_tool_calls_delivery_status "
                    "ON ai_chat_tool_calls (delivery_status)"
                ),
                (
                    "CREATE UNIQUE INDEX ux_ai_chat_tool_run_index "
                    "ON ai_chat_tool_calls (run_id,tool_call_index)"
                ),
                (
                    "CREATE UNIQUE INDEX ux_ai_chat_tool_provider_call "
                    "ON ai_chat_tool_calls (run_id,provider_tool_call_id) "
                    "WHERE provider_tool_call_id IS NOT NULL"
                ),
            ):
                connection.exec_driver_sql(sql)
        connection.execute(
            text(
                "INSERT INTO schema_migrations (name,applied_at) "
                "VALUES (:name,:now)"
            ),
            {"name": MIGRATION_NAME, "now": utcnow_iso()},
        )
        transaction.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()
