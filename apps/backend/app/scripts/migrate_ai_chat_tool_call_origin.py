"""记录 AI Chat 工具调用是否由模型产生。"""

from sqlalchemy import Engine, text

from app.ai_chat.models import utcnow_iso
from app.models import Base

MIGRATION_NAME = "2026_08_16_ai_chat_tool_call_origin"


def migrate(engine: Engine) -> None:
    """为已有工具调用补充模型来源标记，历史结果保持原投递状态。"""
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
            str(row[1])
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(ai_chat_tool_calls)"
            )
        }
        if "requested_by_model" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE ai_chat_tool_calls ADD COLUMN "
                "requested_by_model BOOLEAN NOT NULL DEFAULT 1"
            )
        connection.execute(
            text(
                "INSERT INTO schema_migrations (name,applied_at) "
                "VALUES (:name,:now)"
            ),
            {"name": MIGRATION_NAME, "now": utcnow_iso()},
        )
