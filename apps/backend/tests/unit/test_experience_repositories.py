"""Real-SQLite behavior tests for the experience repositories."""

import logging
from datetime import datetime, timedelta

import pytest

from app.database import Database
from app.models import EvidenceItem, ExperienceItem
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.experience_repository import ExperienceRepository


async def test_repositories_create_and_filter_active_experiences(tmp_path) -> None:
    """Default listing must exclude archived records while preserving search/filter results."""
    database = Database(db_path=tmp_path / "experience.db")
    try:
        async with database.session() as session:
            experiences = ExperienceRepository(session)
            active = await experiences.create(
                ExperienceItem(
                    kind="project",
                    title="Matcher",
                    organization="Kestrel Labs",
                    technologies=["FastAPI"],
                    tags=["AI"],
                )
            )
            ready = await experiences.create(
                ExperienceItem(kind="work", title="Platform", status="ready")
            )
            archived = await experiences.create(
                ExperienceItem(kind="project", title="Old project", status="archived")
            )
            await session.commit()

            assert await experiences.get(active.experience_id) is not None
            assert [item.experience_id for item in await experiences.list()] == [ready.experience_id, active.experience_id]
            assert [item.experience_id for item in await experiences.list(q="kestrel", kind="project")] == [active.experience_id]
            assert [item.experience_id for item in await experiences.list(status="archived")] == [archived.experience_id]
    finally:
        await database.close()


async def test_repositories_preserve_evidence_order(tmp_path) -> None:
    """Evidence expansion must retain the experience's user-defined order."""
    database = Database(db_path=tmp_path / "experience.db")
    try:
        async with database.session() as session:
            experiences = ExperienceRepository(session)
            evidence = EvidenceRepository(session)
            item = await experiences.create(ExperienceItem(kind="project", title="Agent"))
            first = await evidence.create(EvidenceItem(action="First"))
            second = await evidence.create(EvidenceItem(action="Second"))
            await experiences.set_evidence_ids(item.experience_id, [second.id, first.id])
            await session.commit()

            assert [row.id for row in await evidence.get_many_ordered([second.id, first.id])] == [second.id, first.id]
    finally:
        await database.close()


async def test_evidence_reports_one_owner_and_rejects_cross_experience_assignment(tmp_path) -> None:
    """Assigning one evidence row to two experiences must be rejected at the repository boundary."""
    database = Database(db_path=tmp_path / "experience.db")
    try:
        async with database.session() as session:
            experiences = ExperienceRepository(session)
            evidence = EvidenceRepository(session)
            first_experience = await experiences.create(ExperienceItem(kind="project", title="First"))
            second_experience = await experiences.create(ExperienceItem(kind="project", title="Second"))
            item = await evidence.create(EvidenceItem(action="Built service"))
            await experiences.set_evidence_ids(first_experience.experience_id, [item.id])
            await session.commit()

            assert await evidence.find_owner_experience_id(item.id) == first_experience.experience_id
            with pytest.raises(ValueError, match="already belongs"):
                await experiences.set_evidence_ids(second_experience.experience_id, [item.id])
            with pytest.raises(ValueError, match="unsupported experience fields"):
                await experiences.update_fields(
                    second_experience.experience_id, {"evidence_ids": [item.id]}
                )
    finally:
        await database.close()


async def test_repositories_update_and_delete_only_unowned_evidence(tmp_path) -> None:
    """Repository mutations must return stored rows and prevent dangling evidence references."""
    database = Database(db_path=tmp_path / "experience.db")
    try:
        async with database.session() as session:
            experiences = ExperienceRepository(session)
            evidence = EvidenceRepository(session)
            experience = await experiences.create(ExperienceItem(kind="project", title="Before"))
            item = await evidence.create(EvidenceItem(action="Initial"))
            experience_id = experience.experience_id
            evidence_id = item.id

            assert (await experiences.update_fields(experience_id, {"title": "After"})).title == "After"
            assert (await evidence.update_fields(evidence_id, {"result": "Released"})).result == "Released"
            await experiences.set_evidence_ids(experience_id, [evidence_id])
            await session.commit()

        async with database.session() as session:
            experiences = ExperienceRepository(session)
            evidence = EvidenceRepository(session)
            assert (await experiences.get(experience_id)).title == "After"
            assert (await evidence.get(evidence_id)).result == "Released"
            with pytest.raises(ValueError, match="belongs to experience"):
                await evidence.delete(evidence_id)
            assert await experiences.delete(experience_id) is True
            assert await evidence.delete(evidence_id) is True
            await session.commit()

        async with database.session() as session:
            experiences = ExperienceRepository(session)
            evidence = EvidenceRepository(session)
            assert await experiences.get(experience_id) is None
            assert await evidence.get(evidence_id) is None
            assert await experiences.delete(experience_id) is False
            assert await evidence.delete(evidence_id) is False
    finally:
        await database.close()


async def test_repositories_leave_transaction_control_to_the_caller(tmp_path) -> None:
    """A repository create must roll back when its caller does not commit the shared session."""
    database = Database(db_path=tmp_path / "experience.db")
    try:
        async with database.session() as session:
            item = await ExperienceRepository(session).create(ExperienceItem(kind="project", title="Transient"))
            experience_id = item.experience_id

        async with database.session() as session:
            assert await ExperienceRepository(session).get(experience_id) is None
    finally:
        await database.close()


async def test_experience_updates_persist_editable_fields_and_reject_system_fields(tmp_path) -> None:
    """Generic experience updates must not mutate lifecycle, audit, or evidence state."""
    database = Database(db_path=tmp_path / "experience.db")
    try:
        async with database.session() as session:
            experiences = ExperienceRepository(session)
            item = await experiences.create(ExperienceItem(kind="project", title="Before"))
            experience_id = item.experience_id
            await experiences.update_fields(
                experience_id,
                {"title": "After", "organization": "Kestrel", "tags": ["AI"]},
            )
            for field in (
                "status",
                "completeness",
                "archived_at",
                "created_at",
                "updated_at",
                "evidence_ids",
                "experience_id",
            ):
                with pytest.raises(ValueError, match="unsupported experience fields"):
                    await experiences.update_fields(experience_id, {field: "reserved"})
            await session.commit()

        async with database.session() as session:
            saved = await ExperienceRepository(session).get(experience_id)
            assert saved is not None
            assert saved.title == "After"
            assert saved.organization == "Kestrel"
            assert saved.tags == ["AI"]
    finally:
        await database.close()


async def test_experience_system_setters_validate_and_persist_lifecycle_state(tmp_path) -> None:
    """Completeness and status changes must use narrow repository methods with valid values."""
    database = Database(db_path=tmp_path / "experience.db")
    try:
        async with database.session() as session:
            experiences = ExperienceRepository(session)
            item = await experiences.create(ExperienceItem(kind="project", title="Agent"))
            experience_id = item.experience_id
            original_updated_at = item.updated_at

            completed = await experiences.set_completeness(experience_id, 75)
            archived = await experiences.set_status(experience_id, "archived")

            assert completed.completeness == 75
            assert completed.updated_at != original_updated_at
            assert archived.archived_at is not None
            restored = await experiences.set_status(experience_id, "draft")
            assert restored.status == "draft"
            assert restored.archived_at is None
            for value in (-1, 101, True, "75"):
                with pytest.raises(ValueError, match="completeness"):
                    await experiences.set_completeness(experience_id, value)
            with pytest.raises(ValueError, match="status"):
                await experiences.set_status(experience_id, "deleted")
            await session.commit()

        async with database.session() as session:
            saved = await ExperienceRepository(session).get(experience_id)
            assert saved is not None
            assert saved.completeness == 75
            assert saved.status == "draft"
            assert saved.archived_at is None
    finally:
        await database.close()


async def test_status_setter_owns_and_persists_utc_archive_timestamps(tmp_path) -> None:
    """Archive timestamps must be generated by the repository, never supplied by callers."""
    database = Database(db_path=tmp_path / "experience.db")
    try:
        async with database.session() as session:
            experiences = ExperienceRepository(session)
            item = await experiences.create(ExperienceItem(kind="project", title="Agent"))
            experience_id = item.experience_id

            with pytest.raises(TypeError):
                await experiences.set_status(
                    experience_id, "archived", archived_at="2000-01-01T00:00:00+00:00"
                )
            archived = await experiences.set_status(experience_id, "archived")
            assert archived.archived_at is not None
            parsed = datetime.fromisoformat(archived.archived_at)
            assert parsed.tzinfo is not None
            assert parsed.utcoffset() == timedelta(0)
            await session.commit()

        async with database.session() as session:
            saved = await ExperienceRepository(session).get(experience_id)
            assert saved is not None
            assert saved.archived_at is not None
            assert datetime.fromisoformat(saved.archived_at).utcoffset() == timedelta(0)
    finally:
        await database.close()


async def test_evidence_updates_persist_editable_fields_and_reject_system_fields(tmp_path) -> None:
    """Generic evidence updates must only change action, result, and metrics."""
    database = Database(db_path=tmp_path / "experience.db")
    try:
        async with database.session() as session:
            evidence = EvidenceRepository(session)
            item = await evidence.create(EvidenceItem(action="Before"))
            evidence_id = item.id
            await evidence.update_fields(
                evidence_id,
                {"action": "After", "result": "Released", "metrics": "40% faster"},
            )
            for field in ("id", "created_at", "updated_at"):
                with pytest.raises(ValueError, match="unsupported evidence fields"):
                    await evidence.update_fields(evidence_id, {field: "reserved"})
            await session.commit()

        async with database.session() as session:
            saved = await EvidenceRepository(session).get(evidence_id)
            assert saved is not None
            assert (saved.action, saved.result, saved.metrics) == (
                "After",
                "Released",
                "40% faster",
            )
    finally:
        await database.close()


async def test_ordered_evidence_expansion_warns_only_about_missing_ids(tmp_path, caplog) -> None:
    """Missing evidence must be observable without disturbing valid caller order or leaking text."""
    database = Database(db_path=tmp_path / "experience.db")
    try:
        async with database.session() as session:
            evidence = EvidenceRepository(session)
            first = await evidence.create(EvidenceItem(action="Sensitive user action"))
            second = await evidence.create(EvidenceItem(action="Second"))
            await session.commit()

            with caplog.at_level(logging.WARNING, logger="app.repositories.evidence_repository"):
                expanded = await evidence.get_many_ordered(
                    [second.id, 998, first.id, second.id, 999]
                )

            assert [item.id for item in expanded] == [second.id, first.id, second.id]
            assert caplog.messages == [
                "Missing evidence IDs while expanding references: [998, 999]"
            ]
    finally:
        await database.close()
