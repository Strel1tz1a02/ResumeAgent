"""检查并可选清理已退出功能的 SQLite 结构。

默认只读检查。执行 ``--apply`` 前必须显式提供 ``--backup``，脚本会先复制
数据库，再删除空的旧内容列和空的 applications 表；表非空时拒绝执行。
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

LEGACY_COLUMNS = ("cover_letter", "outreach_message", "interview_prep")


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def retire(database: Path, *, apply: bool = False, backup: Path | None = None, keep_legacy_content: bool = False) -> dict[str, object]:
    """检查或清理旧结构；默认不修改数据库。"""
    if not database.exists():
        return {"status": "missing", "database": str(database)}
    if apply and backup is None:
        raise ValueError("--apply requires --backup")
    if apply:
        assert backup is not None
        shutil.copy2(database, backup)

    with sqlite3.connect(database) as connection:
        application_count = (
            connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
            if _table_exists(connection, "applications")
            else 0
        )
        if apply and application_count:
            raise ValueError(f"applications table is not empty: {application_count} row(s)")

        resume_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(resumes)").fetchall()
        } if _table_exists(connection, "resumes") else set()
        legacy_counts = {
            column: connection.execute(
                f"SELECT COUNT(*) FROM resumes WHERE {column} IS NOT NULL AND TRIM({column}) <> ''"
            ).fetchone()[0]
            for column in LEGACY_COLUMNS
            if column in resume_columns
        }
        if apply and not keep_legacy_content and any(legacy_counts.values()):
            raise ValueError(f"legacy resume content is not empty: {legacy_counts}")

        if apply:
            if not keep_legacy_content:
                for column in LEGACY_COLUMNS:
                    if column in resume_columns:
                        connection.execute(f"ALTER TABLE resumes DROP COLUMN {column}")
            if _table_exists(connection, "applications"):
                connection.execute("DROP TABLE applications")
            connection.commit()
        return {
            "status": "applied" if apply else "ready",
            "database": str(database),
            "applications": application_count,
            "legacy_content": legacy_counts,
            "backup": str(backup) if backup else None,
        }


def main() -> None:
    """执行命令行检查或显式迁移。"""
    parser = argparse.ArgumentParser(description="Retire legacy Resume Matcher schema")
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true", help="apply destructive cleanup")
    parser.add_argument("--backup", type=Path, help="backup path required with --apply")
    parser.add_argument("--keep-legacy-content", action="store_true", help="drop only applications table")
    args = parser.parse_args()
    print(retire(args.database, apply=args.apply, backup=args.backup, keep_legacy_content=args.keep_legacy_content))


if __name__ == "__main__":
    main()
