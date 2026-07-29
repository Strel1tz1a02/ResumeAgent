"""Real-SQLite behavior tests for the experience repositories."""

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

            assert (await experiences.update_fields(experience.experience_id, {"title": "After"})).title == "After"
            assert (await evidence.update_fields(item.id, {"result": "Released"})).result == "Released"
            await experiences.set_evidence_ids(experience.experience_id, [item.id])
            with pytest.raises(ValueError, match="belongs to experience"):
                await evidence.delete(item.id)
            assert await experiences.delete(experience.experience_id) is True
            assert await evidence.delete(item.id) is True
            assert await experiences.delete(experience.experience_id) is False
            assert await evidence.delete(item.id) is False
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
