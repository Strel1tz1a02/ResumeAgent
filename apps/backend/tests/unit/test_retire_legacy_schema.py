"""旧结构清理脚本的安全边界测试。"""

import sqlite3

import pytest

from app.scripts.retire_legacy_schema import retire


def _database(path):
    """创建包含旧结构的最小测试数据库。"""
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE resumes (
                resume_id TEXT PRIMARY KEY,
                cover_letter TEXT,
                outreach_message TEXT,
                interview_prep TEXT
            );
            CREATE TABLE applications (
                application_id TEXT PRIMARY KEY,
                job_id TEXT,
                resume_id TEXT,
                status TEXT,
                created_at TEXT
            );
            """
        )


def test_default_mode_is_read_only(tmp_path):
    """默认模式只检查，不改变表结构。"""
    database = tmp_path / "resume.db"
    _database(database)
    result = retire(database)
    assert result["status"] == "ready"
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(resumes)")}
    assert "cover_letter" in columns


def test_apply_requires_backup(tmp_path):
    """破坏性模式必须显式提供备份路径。"""
    database = tmp_path / "resume.db"
    _database(database)
    with pytest.raises(ValueError, match="--backup"):
        retire(database, apply=True)


def test_apply_refuses_non_empty_applications(tmp_path):
    """存在历史 Application 时拒绝删除旧结构。"""
    database = tmp_path / "resume.db"
    backup = tmp_path / "backup.db"
    _database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO applications VALUES ('a', 'j', 'r', 'applied', '2026-01-01')"
        )
    with pytest.raises(ValueError, match="applications table is not empty"):
        retire(database, apply=True, backup=backup)
