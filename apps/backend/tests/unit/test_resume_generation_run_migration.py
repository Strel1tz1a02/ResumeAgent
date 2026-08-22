"""简历生成 Run/Artifact 生命周期拆分迁移测试。"""

import sqlite3

from sqlalchemy import create_engine

from app.scripts.migrate_resume_generation_run_lifecycle import migrate


def test_old_preview_and_confirm_statuses_are_split_without_data_loss(tmp_path) -> None:
    path = tmp_path / "resume-runs.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE resume_generation_runs ("
            "run_id VARCHAR PRIMARY KEY,jd_information_id INTEGER NOT NULL,"
            "request_json JSON NOT NULL,jd_snapshot_json JSON,"
            "experience_snapshots_json JSON,plan_json JSON,resume_data_json JSON,"
            "provenance_json JSON,validation_json JSON,status VARCHAR(16) NOT NULL,"
            "generated_resume_id VARCHAR,error TEXT,created_at VARCHAR NOT NULL,"
            "updated_at VARCHAR NOT NULL,CONSTRAINT old_status CHECK "
            "(status IN ('running','previewed','failed','confirmed')))"
        )
        for run_id, status, resume_id in (
            ("preview", "previewed", None),
            ("confirm", "confirmed", "resume-1"),
        ):
            connection.execute(
                "INSERT INTO resume_generation_runs "
                "(run_id,jd_information_id,request_json,status,generated_resume_id,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (run_id, 1, "{}", status, resume_id, "now", "now"),
            )

    engine = create_engine(f"sqlite:///{path}")
    try:
        migrate(engine)
        migrate(engine)
        with engine.connect() as connection:
            rows = connection.exec_driver_sql(
                "SELECT run_id,status,artifact_status,generated_resume_id "
                "FROM resume_generation_runs ORDER BY run_id"
            ).all()
    finally:
        engine.dispose()

    assert rows == [
        ("confirm", "completed", "confirmed", "resume-1"),
        ("preview", "completed", "previewed", None),
    ]
