"""移除 JD 来源表的迁移测试。"""

from app.db_engine import make_sync_engine
from app.scripts.migrate_jd_import_origin import migrate
from sqlalchemy import inspect


def test_migration_moves_url_drops_raw_text_and_is_idempotent(tmp_path) -> None:
    engine = make_sync_engine(tmp_path / "legacy-jd.db")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE jd_origin ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, raw_text TEXT NOT NULL, "
                "source_url TEXT)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE jd_information ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, jd_origin_id INTEGER NOT NULL, "
                "company VARCHAR(200) NOT NULL DEFAULT '', "
                "job_name VARCHAR(200) NOT NULL DEFAULT '', "
                "type VARCHAR(100) NOT NULL DEFAULT '', "
                "location VARCHAR(200) NOT NULL DEFAULT '', "
                "status VARCHAR(16) NOT NULL DEFAULT 'analysing', "
                "revision INTEGER NOT NULL DEFAULT 0, "
                "FOREIGN KEY(jd_origin_id) REFERENCES jd_origin(id) ON DELETE CASCADE)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE jd_requirements ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, jd_information_id INTEGER NOT NULL, "
                "priority VARCHAR(16) NOT NULL DEFAULT 'normal', content TEXT NOT NULL, "
                "sort_order INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0, "
                "FOREIGN KEY(jd_information_id) REFERENCES jd_information(id) ON DELETE CASCADE)"
            )
            connection.exec_driver_sql(
                "INSERT INTO jd_origin(id, raw_text, source_url) "
                "VALUES (1, 'secret raw text', 'https://example.com/job')"
            )
            connection.exec_driver_sql(
                "INSERT INTO jd_information("
                "id, jd_origin_id, company, job_name, type, location, status, revision"
                ") VALUES (2, 1, 'Acme', 'Engineer', 'backend', 'Shanghai', "
                "'confirmed', 3)"
            )
            connection.exec_driver_sql(
                "INSERT INTO jd_requirements("
                "id, jd_information_id, priority, content, sort_order, revision"
                ") VALUES (3, 2, 'required', 'Python', 0, 1)"
            )

        migrate(engine)
        migrate(engine)

        inspector = inspect(engine)
        assert "jd_origin" not in inspector.get_table_names()
        assert "raw_text" not in {
            column["name"] for column in inspector.get_columns("jd_information")
        }
        with engine.begin() as connection:
            information = connection.exec_driver_sql(
                "SELECT id, source_url, company, status, revision FROM jd_information"
            ).mappings().one()
            requirement = connection.exec_driver_sql(
                "SELECT jd_information_id, content, revision FROM jd_requirements"
            ).mappings().one()
        assert dict(information) == {
            "id": 2,
            "source_url": "https://example.com/job",
            "company": "Acme",
            "status": "confirmed",
            "revision": 3,
        }
        assert dict(requirement) == {
            "jd_information_id": 2,
            "content": "Python",
            "revision": 1,
        }
    finally:
        engine.dispose()


def test_migration_creates_new_schema_without_legacy_tables(tmp_path) -> None:
    engine = make_sync_engine(tmp_path / "new-jd.db")
    try:
        migrate(engine)
        inspector = inspect(engine)
        assert "jd_information" in inspector.get_table_names()
        assert "jd_requirements" in inspector.get_table_names()
        assert "jd_origin" not in inspector.get_table_names()
    finally:
        engine.dispose()
