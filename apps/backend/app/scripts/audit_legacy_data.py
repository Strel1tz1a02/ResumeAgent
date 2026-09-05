"""只读盘点已退出功能留下的 SQLite 数据。

该脚本不执行迁移、不删除记录，供决定是否清理兼容字段前使用。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


LEGACY_COLUMNS = ("cover_letter", "outreach_message", "interview_prep")


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    """判断表是否存在。"""
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def audit(database_path: Path) -> dict[str, Any]:
    """统计旧功能相关表和字段的记录数，不改变数据库。"""
    if not database_path.exists():
        return {"database": str(database_path), "status": "missing", "tables": {}}

    with sqlite3.connect(database_path) as connection:
        tables: dict[str, Any] = {}
        if _table_exists(connection, "applications"):
            application_rows = connection.execute(
                "SELECT application_id, job_id, resume_id, status FROM applications ORDER BY created_at"
            ).fetchall()
            tables["applications"] = {
                "rows": len(application_rows),
                "records": [
                    {
                        "application_id": row[0],
                        "job_id": row[1],
                        "resume_id": row[2],
                        "status": row[3],
                    }
                    for row in application_rows
                ],
            }
        if _table_exists(connection, "resumes"):
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(resumes)").fetchall()
            }
            legacy = {
                column: connection.execute(
                    f"SELECT COUNT(*) FROM resumes WHERE {column} IS NOT NULL AND TRIM({column}) <> ''"
                ).fetchone()[0]
                for column in LEGACY_COLUMNS
                if column in columns
            }
            tables["resumes"] = {"rows": connection.execute("SELECT COUNT(*) FROM resumes").fetchone()[0], "legacy_content": legacy}
        return {"database": str(database_path), "status": "ok", "tables": tables}


def main() -> None:
    """运行审计并输出 JSON。"""
    parser = argparse.ArgumentParser(description="Audit legacy Resume Matcher data")
    parser.add_argument("database", type=Path, help="SQLite database path")
    args = parser.parse_args()
    print(json.dumps(audit(args.database), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
