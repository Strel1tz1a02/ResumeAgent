"""将 ExperienceItem.evidence_ids JSON 迁移到有序关系表。"""

from __future__ import annotations

import json

from sqlalchemy import Engine, inspect, text

from app.models import Base, _utcnow_iso

MIGRATION_NAME = "2026_08_03_experience_evidence_items"


def _ids(value: object) -> list[int]:
    """按首次出现顺序读取旧 JSON 中的有效整数 ID。"""
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        return []
    result: list[int] = []
    seen: set[int] = set()
    for item in value:
        if isinstance(item, int) and not isinstance(item, bool) and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def migrate(engine: Engine) -> None:
    """幂等迁移已有顺序与归属，随后删除旧 JSON 列。"""
    Base.metadata.create_all(engine)
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
        columns = {
            column["name"] for column in inspector.get_columns("experience_items")
        }
        if "evidence_ids" in columns:
            experiences = connection.execute(
                text(
                    "SELECT experience_id, evidence_ids FROM experience_items "
                    "ORDER BY experience_id"
                )
            ).mappings()
            claimed: set[int] = set()
            for experience in experiences:
                position = 0
                for evidence_id in _ids(experience["evidence_ids"]):
                    if evidence_id in claimed:
                        continue
                    exists = connection.scalar(
                        text("SELECT 1 FROM evidence_items WHERE id = :id"),
                        {"id": evidence_id},
                    )
                    if not exists:
                        continue
                    connection.execute(
                        text(
                            "INSERT INTO experience_evidence_items "
                            "(experience_id, evidence_id, position) "
                            "VALUES (:experience_id, :evidence_id, :position)"
                        ),
                        {
                            "experience_id": int(experience["experience_id"]),
                            "evidence_id": evidence_id,
                            "position": position,
                        },
                    )
                    claimed.add(evidence_id)
                    position += 1
            connection.exec_driver_sql(
                "ALTER TABLE experience_items DROP COLUMN evidence_ids"
            )
        connection.execute(
            text("INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :now)"),
            {"name": MIGRATION_NAME, "now": _utcnow_iso()},
        )
