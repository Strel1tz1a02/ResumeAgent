"""移除持久化的 JD 原文，并将唯一 URL 迁移到 JD 信息表。"""

from __future__ import annotations

from sqlalchemy import Engine, text

from app.ai_chat.models import utcnow_iso
from app.models import Base

MIGRATION_NAME = "2026_08_13_jd_import_origin"


def migrate(engine: Engine) -> None:
    """幂等地将旧版三表 JD 聚合迁移为两表结构。"""
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            with connection.begin():
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
                    "PRAGMA table_info(jd_information)"
                ).mappings().all()
                names = {column["name"] for column in columns}
                if columns and "jd_origin_id" in names:
                    _rebuild_legacy_tables(connection)

                connection.execute(
                    text(
                        "INSERT INTO schema_migrations(name, applied_at) "
                        "VALUES (:name, :now)"
                    ),
                    {"name": MIGRATION_NAME, "now": utcnow_iso()},
                )
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()

    Base.metadata.create_all(engine)


def _rebuild_legacy_tables(connection) -> None:
    connection.exec_driver_sql(
        "ALTER TABLE jd_requirements RENAME TO jd_requirements_legacy"
    )
    connection.exec_driver_sql(
        "ALTER TABLE jd_information RENAME TO jd_information_legacy"
    )
    connection.exec_driver_sql(
        "CREATE TABLE jd_information ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, source_url TEXT, "
        "company VARCHAR(200) NOT NULL DEFAULT '', "
        "job_name VARCHAR(200) NOT NULL DEFAULT '', "
        "type VARCHAR(100) NOT NULL DEFAULT '', "
        "location VARCHAR(200) NOT NULL DEFAULT '', "
        "status VARCHAR(16) NOT NULL DEFAULT 'incomplete', "
        "revision INTEGER NOT NULL DEFAULT 0, "
        "CONSTRAINT ck_jd_information_status "
        "CHECK (status IN ('incomplete', 'confirmed')), "
        "CONSTRAINT ck_jd_information_revision CHECK (revision >= 0))"
    )
    connection.exec_driver_sql(
        "INSERT INTO jd_information("
        "id, source_url, company, job_name, type, location, status, revision) "
        "SELECT information.id, origin.source_url, information.company, "
        "information.job_name, information.type, information.location, "
        "CASE WHEN information.status = 'confirmed' THEN 'confirmed' "
        "ELSE 'incomplete' END, information.revision "
        "FROM jd_information_legacy AS information "
        "JOIN jd_origin AS origin ON origin.id = information.jd_origin_id"
    )
    connection.exec_driver_sql(
        "CREATE TABLE jd_requirements ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, jd_information_id INTEGER NOT NULL, "
        "priority VARCHAR(16) NOT NULL DEFAULT 'normal', content TEXT NOT NULL, "
        "sort_order INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0, "
        "CONSTRAINT ck_jd_requirement_priority "
        "CHECK (priority IN ('required', 'preferred', 'normal')), "
        "CONSTRAINT ck_jd_requirement_sort_order CHECK (sort_order >= 0), "
        "CONSTRAINT ck_jd_requirement_revision CHECK (revision >= 0), "
        "FOREIGN KEY(jd_information_id) REFERENCES jd_information(id) ON DELETE CASCADE)"
    )
    connection.exec_driver_sql(
        "INSERT INTO jd_requirements("
        "id, jd_information_id, priority, content, sort_order, revision) "
        "SELECT id, jd_information_id, priority, content, sort_order, revision "
        "FROM jd_requirements_legacy"
    )
    connection.exec_driver_sql("DROP TABLE jd_requirements_legacy")
    connection.exec_driver_sql("DROP TABLE jd_information_legacy")
    connection.exec_driver_sql("DROP TABLE jd_origin")
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_jd_information_status "
        "ON jd_information(status)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_jd_requirements_jd_information_id "
        "ON jd_requirements(jd_information_id)"
    )
