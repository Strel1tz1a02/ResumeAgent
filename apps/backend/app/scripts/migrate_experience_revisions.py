"""把字段状态中的旧 revision 迁移到统一 revision 表。"""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text

from app.models import Base, _utcnow_iso
from app.experience.services.experience_fields import EXPERIENCE_TARGET_KEYS, save_unit_key

MIGRATION_NAME = "2026_08_04_experience_revisions"


def migrate(engine: Engine) -> None:
    """幂等回填数据单元和集合 revision，并移除旧 revision 列。"""
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

        columns = {
            column["name"]
            for column in inspect(connection).get_columns("experience_field_states")
        }
        has_legacy_revision = "revision" in columns
        revision_expression = "revision" if has_legacy_revision else "0 AS revision"
        rows = connection.execute(
            text(
                "SELECT experience_id,target_key,ref_id," + revision_expression + " "
                "FROM experience_field_states ORDER BY experience_id,id"
            )
        ).mappings().all()

        targets: dict[tuple[int, str, str, int], int] = {}
        for row in rows:
            experience_id = int(row["experience_id"])
            target_key = str(row["target_key"])
            ref_id = int(row["ref_id"] or 0)
            revision = int(row["revision"] or 0)
            if target_key == "evidence_new":
                target = (experience_id, "collection", "evidence", 0)
            elif ref_id > 0:
                target = (experience_id, "unit", "evidence", ref_id)
            elif target_key in EXPERIENCE_TARGET_KEYS:
                target = (
                    experience_id,
                    "unit",
                    save_unit_key(target_key),
                    0,
                )
            else:
                continue
            targets[target] = max(targets.get(target, 0), revision)

        now = _utcnow_iso()
        for (experience_id, scope, unit_key, ref_id), revision in targets.items():
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO experience_revisions "
                    "(experience_id,scope,unit_key,ref_id,revision,created_at,updated_at) "
                    "VALUES (:experience_id,:scope,:unit_key,:ref_id,:revision,:now,:now)"
                ),
                {
                    "experience_id": experience_id,
                    "scope": scope,
                    "unit_key": unit_key,
                    "ref_id": ref_id,
                    "revision": revision,
                    "now": now,
                },
            )

        if has_legacy_revision:
            connection.exec_driver_sql(
                "ALTER TABLE experience_field_states DROP COLUMN revision"
            )
        connection.execute(
            text("INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :now)"),
            {"name": MIGRATION_NAME, "now": now},
        )
