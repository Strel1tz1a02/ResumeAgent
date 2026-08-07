"""一次性迁移经历原文字段并回填字段状态。"""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text

from app.models import Base, _utcnow_iso
from app.experience.services.experience_fields import (
    EVIDENCE_TARGET_KEYS,
    EXPERIENCE_TARGET_KEYS,
    field_status,
)

MIGRATION_NAME = "2026_08_01_experience_field_states"


def migrate(engine: Engine) -> None:
    """在接收请求前移除原文列，并幂等回填全部字段状态。"""
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name VARCHAR(200) PRIMARY KEY, applied_at VARCHAR NOT NULL)"
        )
        columns = {
            column["name"]
            for column in inspect(connection).get_columns("experience_items")
        } if inspect(connection).has_table("experience_items") else set()
        if "raw_input" in columns:
            connection.exec_driver_sql(
                "ALTER TABLE experience_items DROP COLUMN raw_input"
            )

    Base.metadata.create_all(engine)

    with engine.begin() as connection:
        applied = connection.scalar(
            text("SELECT 1 FROM schema_migrations WHERE name = :name"),
            {"name": MIGRATION_NAME},
        )
        if applied:
            return
        experiences = connection.execute(
            text("SELECT * FROM experience_items ORDER BY experience_id")
        ).mappings().all()
        for experience in experiences:
            experience_id = int(experience["experience_id"])
            for key in EXPERIENCE_TARGET_KEYS:
                connection.execute(
                    text(
                        "INSERT OR IGNORE INTO experience_field_states "
                        "(experience_id,target_key,ref_id,status,created_at,updated_at) "
                        "VALUES (:experience_id,:target_key,0,:status,:now,:now)"
                    ),
                    {
                        "experience_id": experience_id,
                        "target_key": key,
                        "status": field_status(key, experience.get(key), experience),
                        "now": _utcnow_iso(),
                    },
                )
            evidence_ids = experience.get("evidence_ids") or []
            if isinstance(evidence_ids, str):
                import json

                evidence_ids = json.loads(evidence_ids)
            for evidence_id in evidence_ids:
                evidence = connection.execute(
                    text("SELECT * FROM evidence_items WHERE id = :id"), {"id": evidence_id}
                ).mappings().first()
                if evidence is None:
                    continue
                for key in EVIDENCE_TARGET_KEYS:
                    connection.execute(
                        text(
                            "INSERT OR IGNORE INTO experience_field_states "
                            "(experience_id,target_key,ref_id,status,created_at,updated_at) "
                            "VALUES (:experience_id,:target_key,:ref_id,:status,:now,:now)"
                        ),
                        {
                            "experience_id": experience_id,
                            "target_key": key,
                            "ref_id": int(evidence_id),
                            "status": field_status(key, evidence.get(key), evidence),
                            "now": _utcnow_iso(),
                        },
                    )
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO experience_field_states "
                    "(experience_id,target_key,ref_id,status,created_at,updated_at) "
                    "VALUES (:experience_id,'evidence_new',0,:status,:now,:now)"
                ),
                {
                    "experience_id": experience_id,
                    "status": "complete" if evidence_ids else "incomplete",
                    "now": _utcnow_iso(),
                },
            )
        connection.execute(
            text("INSERT INTO schema_migrations (name, applied_at) VALUES (:name, :now)"),
            {"name": MIGRATION_NAME, "now": _utcnow_iso()},
        )
