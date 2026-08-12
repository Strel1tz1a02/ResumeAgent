"""将 Evidence 契约从 action/result/metrics 升级为 background/action/result。"""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text

from app.experience.models import utcnow_iso

MIGRATION_NAME = "2026_08_12_evidence_background"


def migrate(engine: Engine) -> None:
    """添加 background 并替换字段状态；旧 metrics 数据不做语义迁移。"""
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name VARCHAR(200) PRIMARY KEY, applied_at VARCHAR NOT NULL)"
        )
        applied = connection.scalar(
            text("SELECT 1 FROM schema_migrations WHERE name = :name"),
            {"name": MIGRATION_NAME},
        )
        if applied:
            return

        inspector = inspect(connection)
        if inspector.has_table("evidence_items"):
            columns = {
                column["name"] for column in inspector.get_columns("evidence_items")
            }
            if "background" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE evidence_items ADD COLUMN background TEXT"
                )

        if inspector.has_table("experience_field_states"):
            connection.execute(
                text(
                    "DELETE FROM experience_field_states "
                    "WHERE target_key = 'metrics' AND ref_id > 0"
                )
            )
            rows = connection.execute(
                text(
                    "SELECT link.experience_id, evidence.id, evidence.background "
                    "FROM experience_evidence_items AS link "
                    "JOIN evidence_items AS evidence ON evidence.id = link.evidence_id"
                )
            ).mappings()
            now = utcnow_iso()
            for row in rows:
                background = row["background"]
                status = (
                    "complete"
                    if isinstance(background, str) and background.strip()
                    else "incomplete"
                )
                connection.execute(
                    text(
                        "INSERT OR IGNORE INTO experience_field_states "
                        "(experience_id,target_key,ref_id,status,created_at,updated_at) "
                        "VALUES (:experience_id,'background',:ref_id,:status,:now,:now)"
                    ),
                    {
                        "experience_id": int(row["experience_id"]),
                        "ref_id": int(row["id"]),
                        "status": status,
                        "now": now,
                    },
                )

        connection.execute(
            text(
                "INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :now)"
            ),
            {"name": MIGRATION_NAME, "now": utcnow_iso()},
        )
