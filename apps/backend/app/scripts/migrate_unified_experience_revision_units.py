"""把 save_unit/evidence 两类 revision 合并为统一 unit。"""

from __future__ import annotations

from sqlalchemy import Engine, text

from app.models import Base
from app.experience.models import utcnow_iso

MIGRATION_NAME = "2026_08_05_unified_experience_revision_units"


def migrate(engine: Engine) -> None:
    """幂等重建旧 SQLite 表，并保留每个数据单元的最大 revision。"""
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
                "WHERE type = 'table' AND name = 'experience_revisions'"
            )
        ) or ""
        if "save_unit" in table_sql or "'evidence'" in table_sql:
            connection.exec_driver_sql(
                "CREATE TABLE experience_revisions_unified ("
                "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,"
                "experience_id INTEGER NOT NULL REFERENCES experience_items(experience_id) ON DELETE CASCADE,"
                "scope VARCHAR(16) NOT NULL CONSTRAINT ck_experience_revision_scope "
                "CHECK (scope IN ('unit', 'collection')) ,"
                "unit_key VARCHAR(80) NOT NULL,"
                "ref_id INTEGER NOT NULL DEFAULT 0,"
                "revision INTEGER NOT NULL DEFAULT 0 CONSTRAINT ck_experience_revision_nonnegative "
                "CHECK (revision >= 0),"
                "created_at VARCHAR NOT NULL,"
                "updated_at VARCHAR NOT NULL,"
                "CONSTRAINT uq_experience_revision_target "
                "UNIQUE (experience_id, scope, unit_key, ref_id))"
            )
            connection.exec_driver_sql(
                "INSERT INTO experience_revisions_unified "
                "(experience_id, scope, unit_key, ref_id, revision, created_at, updated_at) "
                "SELECT experience_id, "
                "CASE WHEN scope IN ('save_unit', 'evidence') THEN 'unit' ELSE scope END, "
                "unit_key, ref_id, MAX(revision), MIN(created_at), MAX(updated_at) "
                "FROM experience_revisions "
                "GROUP BY experience_id, "
                "CASE WHEN scope IN ('save_unit', 'evidence') THEN 'unit' ELSE scope END, "
                "unit_key, ref_id"
            )
            connection.exec_driver_sql("DROP TABLE experience_revisions")
            connection.exec_driver_sql(
                "ALTER TABLE experience_revisions_unified RENAME TO experience_revisions"
            )
            connection.exec_driver_sql(
                "CREATE INDEX ix_experience_revisions_experience_id "
                "ON experience_revisions (experience_id)"
            )

        connection.execute(
            text("INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :now)"),
            {"name": MIGRATION_NAME, "now": utcnow_iso()},
        )
