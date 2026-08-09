"""Fixtures owned by the memory module tests."""

import pytest


@pytest.fixture
async def isolated_db(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Run memory persistence against a disposable SQLite database."""
    import app.database as database_module
    from app.database import Database

    test_db = Database(db_path=tmp_path / "memory_test.db")
    monkeypatch.setattr(database_module, "db", test_db)
    try:
        yield test_db
    finally:
        await test_db.close()
