"""从通用 AI Chat Run 中移除业务专属结果状态。"""

from sqlalchemy import Engine, text

from app.ai_chat.models import utcnow_iso

MIGRATION_NAME = "2026_08_14_remove_ai_chat_run_result"


def migrate(engine: Engine) -> None:
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
            "PRAGMA table_info(ai_chat_runs)"
        ).mappings().all()
        if "result" in {column["name"] for column in columns}:
            connection.exec_driver_sql("ALTER TABLE ai_chat_runs DROP COLUMN result")
        connection.execute(
            text(
                "INSERT INTO schema_migrations (name, applied_at) "
                "VALUES (:name, :now)"
            ),
            {"name": MIGRATION_NAME, "now": utcnow_iso()},
        )
