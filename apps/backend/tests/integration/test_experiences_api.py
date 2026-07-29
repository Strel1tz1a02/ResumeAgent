"""Integration contracts for the person-level experience library API."""

from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
import pytest

from app.database import Database
from app.main import app
from app.repositories.experience_repository import ExperienceStaleWriteError
from app.schemas.experiences import ExperienceCreate, ExperienceUpdate
from app.services.experience_service import ExperienceConflictError, ExperienceService


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_import_text_persists_exact_raw_input_without_llm(isolated_db) -> None:
    """Changing import to parse, trim, or skip persistence must fail this contract."""
    raw_text = "  Built a campus recruiting assistant.\n"

    with patch("app.llm.complete_json") as llm:
        async with _client() as client:
            response = await client.post(
                "/api/v1/experiences/import-text", json={"text": raw_text}
            )

    assert response.status_code == 201
    payload = response.json()
    assert payload["raw_input"] == raw_text
    assert payload["kind"] == "other"
    assert payload["title"] == ""
    assert payload["status"] == "draft"
    assert payload["evidence_ids"] == []
    assert payload["evidence_items"] == []
    assert payload["completeness"] == 0
    llm.assert_not_called()

    async with _client() as client:
        stored = await client.get(f"/api/v1/experiences/{payload['experience_id']}")
    assert stored.status_code == 200
    assert stored.json()["raw_input"] == raw_text


async def test_import_text_rejects_blank_and_oversized_requests(isolated_db) -> None:
    """Removing import input bounds or blank validation must fail this contract."""
    async with _client() as client:
        blank = await client.post("/api/v1/experiences/import-text", json={"text": " \n\t "})
        oversized = await client.post(
            "/api/v1/experiences/import-text", json={"text": "x" * 20_001}
        )

    assert blank.status_code == 422
    assert oversized.status_code == 422


async def test_manual_crud_list_search_and_detail_contract(isolated_db) -> None:
    """Breaking create, update, persistence, detail expansion, or query forwarding must fail."""
    create_payload = {
        "kind": "project",
        "title": "Resume Matcher",
        "organization": "Campus Lab",
        "role": "Backend developer",
        "start_date": "2025-01",
        "is_current": True,
        "raw_input": "Hand-entered project",
        "background": "Built a real-time matching service.",
        "technologies": [" Python ", "FastAPI", "python"],
        "tags": [" ai ", "AI", "career"],
        "notes": "Initial notes",
    }
    async with _client() as client:
        created = await client.post("/api/v1/experiences", json=create_payload)

        assert created.status_code == 201
        payload = created.json()
        experience_id = payload["experience_id"]
        assert payload["title"] == "Resume Matcher"
        assert payload["technologies"] == ["Python", "FastAPI"]
        assert payload["tags"] == ["ai", "career"]
        assert payload["completeness"] == 55

        listed = await client.get(
            "/api/v1/experiences",
            params={"q": "campus", "kind": "project", "status": "active"},
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["experience_id"] == experience_id

        patched = await client.patch(
            f"/api/v1/experiences/{experience_id}",
            json={"organization": "Campus Careers", "is_current": False, "end_date": "2026-07"},
        )
        assert patched.status_code == 200
        assert patched.json()["organization"] == "Campus Careers"
        assert patched.json()["end_date"] == "2026-07"
        assert patched.json()["completeness"] == 55

        detail = await client.get(f"/api/v1/experiences/{experience_id}")

    assert detail.status_code == 200
    assert detail.json()["experience_id"] == experience_id
    assert detail.json()["evidence_items"] == []
    assert "action" in detail.json()["missing_dimensions"]


async def test_missing_and_server_owned_fields_are_rejected(isolated_db) -> None:
    """Allowing missing records or client-set computed/lifecycle fields must fail."""
    async with _client() as client:
        missing = await client.get("/api/v1/experiences/99999")
        forbidden_create = await client.post(
            "/api/v1/experiences",
            json={"title": "Nope", "completeness": 100, "status": "ready"},
        )
        created = await client.post("/api/v1/experiences", json={"title": "Safe"})
        forbidden_patch = await client.patch(
            f"/api/v1/experiences/{created.json()['experience_id']}",
            json={"completeness": 100, "created_at": "2020-01-01"},
        )

    assert missing.status_code == 404
    assert forbidden_create.status_code == 422
    assert forbidden_patch.status_code == 422


async def test_patch_rejects_current_and_end_date_conflicts_in_merged_state(isolated_db) -> None:
    """Validating only sparse patch fields would permit contradictory stored dates."""
    async with _client() as client:
        current = await client.post(
            "/api/v1/experiences", json={"title": "Current", "is_current": True}
        )
        dated = await client.post(
            "/api/v1/experiences", json={"title": "Dated", "end_date": "2026-07"}
        )
        add_end_date = await client.patch(
            f"/api/v1/experiences/{current.json()['experience_id']}",
            json={"end_date": "2026-07"},
        )
        mark_current = await client.patch(
            f"/api/v1/experiences/{dated.json()['experience_id']}",
            json={"is_current": True},
        )

    assert add_end_date.status_code == 422
    assert mark_current.status_code == 422


async def test_failed_import_rolls_back_its_uncommitted_record(isolated_db) -> None:
    """Dropping service rollback after a post-insert failure would leave a ghost draft."""
    with patch(
        "app.services.experience_service.ExperienceRepository.set_completeness",
        new_callable=AsyncMock,
        side_effect=RuntimeError("score write failed"),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            failed = await client.post(
                "/api/v1/experiences/import-text", json={"text": "Transient import"}
            )

    assert failed.status_code == 500
    async with _client() as client:
        listed = await client.get("/api/v1/experiences")
    assert listed.status_code == 200
    assert listed.json() == {"items": [], "total": 0}


async def test_patch_rejects_null_for_non_nullable_persisted_fields(isolated_db) -> None:
    """Forwarding explicit nulls to non-null database columns must not become 500 errors."""
    async with _client() as client:
        created = await client.post(
            "/api/v1/experiences",
            json={"kind": "project", "title": "Stable", "raw_input": "source"},
        )
        experience_id = created.json()["experience_id"]

    responses = []
    for payload in (
        {"kind": None},
        {"title": None},
        {"is_current": None},
        {"raw_input": None},
        {"technologies": None},
        {"tags": None},
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            responses.append(
                await client.patch(f"/api/v1/experiences/{experience_id}", json=payload)
            )

    assert [response.status_code for response in responses] == [422] * 6
    async with _client() as client:
        detail = await client.get(f"/api/v1/experiences/{experience_id}")
    assert detail.json()["kind"] == "project"
    assert detail.json()["title"] == "Stable"
    assert detail.json()["raw_input"] == "source"


async def test_patch_returns_conflict_without_overwriting_a_stale_winner(isolated_db) -> None:
    """Mapping stale conditional writes as success would let clients lose a winner's edit."""
    async with _client() as client:
        created = await client.post("/api/v1/experiences", json={"title": "Winner"})
        experience_id = created.json()["experience_id"]

    with patch(
        "app.services.experience_service.ExperienceRepository.update_fields_if_current",
        new_callable=AsyncMock,
        side_effect=ExperienceStaleWriteError("experience update is stale"),
    ):
        async with _client() as client:
            stale = await client.patch(
                f"/api/v1/experiences/{experience_id}", json={"title": "Loser"}
            )

    assert stale.status_code == 409
    async with _client() as client:
        stored = await client.get(f"/api/v1/experiences/{experience_id}")
    assert stored.status_code == 200
    assert stored.json()["title"] == "Winner"


async def test_full_patch_flow_rejects_stale_writer_after_completeness_recalculation(
    tmp_path, monkeypatch
) -> None:
    """A fixed clock must not let completeness recalculation restore a stale version token."""
    frozen_time = "2030-01-01T00:00:00+00:00"
    monkeypatch.setattr(
        "app.repositories.experience_repository._updated_at", lambda: frozen_time
    )
    database = Database(db_path=tmp_path / "experience.db")
    try:
        async with database.session() as session:
            created = await ExperienceService(session).create(ExperienceCreate(title="Original"))
            experience_id = created.experience_id

        async with database.session() as winner_session:
            async with database.session() as stale_session:
                winner_service = ExperienceService(winner_session)
                stale_service = ExperienceService(stale_session)
                original_conditional_update = stale_service._experiences.update_fields_if_current

                async def commit_winner_before_stale_write(
                    stale_id: int, observed_updated_at: str, fields: dict
                ):
                    await stale_session.rollback()
                    winner = await winner_service.patch(
                        stale_id,
                        ExperienceUpdate(
                            title="Winner",
                            background="Made campus recruiting faster.",
                        ),
                    )
                    assert winner.completeness == 25
                    return await original_conditional_update(
                        stale_id, observed_updated_at, fields
                    )

                monkeypatch.setattr(
                    stale_service._experiences,
                    "update_fields_if_current",
                    commit_winner_before_stale_write,
                )
                with pytest.raises(ExperienceConflictError):
                    await stale_service.patch(
                        experience_id,
                        ExperienceUpdate(title="Loser"),
                    )

        async with database.session() as session:
            stored = await ExperienceService(session).get(experience_id)
            assert stored.title == "Winner"
            assert stored.background == "Made campus recruiting faster."
            assert stored.completeness == 25
            assert stored.updated_at > frozen_time
    finally:
        await database.close()
