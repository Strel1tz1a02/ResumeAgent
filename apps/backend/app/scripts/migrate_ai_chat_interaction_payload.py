"""将 Tool Call 的审批专用旧列名统一为 Interaction 载荷。"""

from sqlalchemy import Engine, text

from app.ai_chat.models import utcnow_iso
from app.models import Base

MIGRATION_NAME = "2026_08_17_ai_chat_interaction_payload"


def migrate(engine: Engine) -> None:
    """幂等重命名旧列，并保留所有已持久化审批或问题批次。"""
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
        names = {column["name"] for column in columns}
        if "proposal_payload" in names and "interaction_payload" not in names:
            connection.exec_driver_sql(
                "ALTER TABLE ai_chat_tool_calls "
                "RENAME COLUMN proposal_payload TO interaction_payload"
            )
        elif "proposal_payload" in names:
            raise RuntimeError("Tool Call contains both legacy and unified payload columns")

        connection.execute(
            text(
                "INSERT INTO schema_migrations (name,applied_at) "
                "VALUES (:name,:now)"
            ),
            {"name": MIGRATION_NAME, "now": utcnow_iso()},
        )
