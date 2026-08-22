"""拆分简历生成的通用 Run 状态与领域 Artifact 状态。"""

from sqlalchemy import Engine, text

from app.ai_chat.models import utcnow_iso
from app.models import Base

MIGRATION_NAME = "2026_08_17_resume_generation_run_lifecycle"


def migrate(engine: Engine) -> None:
    """幂等重建 SQLite 表，并保留旧版运行记录。"""
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

        columns = connection.exec_driver_sql(
            "PRAGMA table_info(resume_generation_runs)"
        ).mappings().all()
        table_sql = connection.scalar(
            text(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='resume_generation_runs'"
            )
        ) or ""
        names = {column["name"] for column in columns}
        if "artifact_status" not in names or "'completed'" not in table_sql:
            connection.exec_driver_sql(
                "CREATE TABLE resume_generation_runs_lifecycle_v2 ("
                "run_id VARCHAR NOT NULL PRIMARY KEY,"
                "jd_information_id INTEGER NOT NULL,request_json JSON NOT NULL,"
                "jd_snapshot_json JSON,experience_snapshots_json JSON,"
                "plan_json JSON,resume_data_json JSON,provenance_json JSON,"
                "validation_json JSON,status VARCHAR(16) NOT NULL,"
                "artifact_status VARCHAR(16) NOT NULL,generated_resume_id VARCHAR,"
                "error TEXT,created_at VARCHAR NOT NULL,updated_at VARCHAR NOT NULL,"
                "CONSTRAINT ck_resume_generation_run_status CHECK (status IN "
                "('running','suspended','completed','failed','cancelled')) ,"
                "CONSTRAINT ck_resume_generation_artifact_status CHECK "
                "(artifact_status IN ('pending','previewed','confirmed')))"
            )
            artifact_expression = (
                "artifact_status"
                if "artifact_status" in names
                else "CASE status WHEN 'previewed' THEN 'previewed' "
                "WHEN 'confirmed' THEN 'confirmed' ELSE 'pending' END"
            )
            run_expression = (
                "CASE status WHEN 'previewed' THEN 'completed' "
                "WHEN 'confirmed' THEN 'completed' ELSE status END"
            )
            connection.exec_driver_sql(
                "INSERT INTO resume_generation_runs_lifecycle_v2 "
                "(run_id,jd_information_id,request_json,jd_snapshot_json,"
                "experience_snapshots_json,plan_json,resume_data_json,"
                "provenance_json,validation_json,status,artifact_status,"
                "generated_resume_id,error,created_at,updated_at) SELECT "
                "run_id,jd_information_id,request_json,jd_snapshot_json,"
                "experience_snapshots_json,plan_json,resume_data_json,"
                f"provenance_json,validation_json,{run_expression},"
                f"{artifact_expression},generated_resume_id,error,created_at,updated_at "
                "FROM resume_generation_runs"
            )
            connection.exec_driver_sql("DROP TABLE resume_generation_runs")
            connection.exec_driver_sql(
                "ALTER TABLE resume_generation_runs_lifecycle_v2 "
                "RENAME TO resume_generation_runs"
            )
            for sql in (
                "CREATE INDEX ix_resume_generation_runs_jd_information_id "
                "ON resume_generation_runs (jd_information_id)",
                "CREATE INDEX ix_resume_generation_runs_status "
                "ON resume_generation_runs (status)",
                "CREATE INDEX ix_resume_generation_runs_artifact_status "
                "ON resume_generation_runs (artifact_status)",
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
