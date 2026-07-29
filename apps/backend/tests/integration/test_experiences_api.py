"""Integration contracts for the person-level experience library API."""

from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
import pytest

from app.database import Database
from app.main import app
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.experience_repository import ExperienceRepository
from app.repositories.experience_repository import ExperienceStaleWriteError
from app.schemas.experiences import ExperienceCreate, ExperienceUpdate
from app.services.evidence_service import EvidenceService
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


async def test_create_evidence_appends_it_and_returns_expanded_experience(isolated_db) -> None:
    """Dropping the JSON reference after inserting evidence would hide a valid fact from clients."""
    async with _client() as client:
        created = await client.post(
            "/api/v1/experiences",
            json={"kind": "project", "title": "Evidence API"},
        )
        experience_id = created.json()["experience_id"]
        evidence = await client.post(
            f"/api/v1/experiences/{experience_id}/evidence",
            json={"action": "Built route", "result": "Returned expanded detail", "metrics": "1 API"},
        )

    assert evidence.status_code == 201
    payload = evidence.json()
    assert len(payload["evidence_ids"]) == 1
    assert payload["evidence_items"] == [
        {
            "id": payload["evidence_ids"][0],
            "action": "Built route",
            "result": "Returned expanded detail",
            "metrics": "1 API",
            "created_at": payload["evidence_items"][0]["created_at"],
            "updated_at": payload["evidence_items"][0]["updated_at"],
        }
    ]


async def test_patch_evidence_requires_ownership_and_hides_cross_experience_rows(isolated_db) -> None:
    """Removing the JSON-membership check would let one experience edit another's evidence."""
    async with _client() as client:
        first = await client.post("/api/v1/experiences", json={"title": "First"})
        second = await client.post("/api/v1/experiences", json={"title": "Second"})
        first_id = first.json()["experience_id"]
        second_id = second.json()["experience_id"]
        evidence = await client.post(
            f"/api/v1/experiences/{first_id}/evidence", json={"action": "Original"}
        )
        evidence_id = evidence.json()["evidence_ids"][0]
        denied = await client.patch(
            f"/api/v1/experiences/{second_id}/evidence/{evidence_id}",
            json={"action": "Stolen"},
        )
        updated = await client.patch(
            f"/api/v1/experiences/{first_id}/evidence/{evidence_id}",
            json={"action": "Corrected", "result": "Saved"},
        )

    assert denied.status_code == 404
    assert updated.status_code == 200
    assert updated.json()["evidence_items"][0]["action"] == "Corrected"
    assert updated.json()["evidence_items"][0]["result"] == "Saved"


async def test_delete_evidence_removes_row_and_json_reference_atomically(isolated_db) -> None:
    """Leaving either the evidence row or its reference behind creates inconsistent detail responses."""
    async with _client() as client:
        experience = await client.post("/api/v1/experiences", json={"title": "Delete proof"})
        experience_id = experience.json()["experience_id"]
        created = await client.post(
            f"/api/v1/experiences/{experience_id}/evidence", json={"action": "Disposable"}
        )
        evidence_id = created.json()["evidence_ids"][0]
        deleted = await client.delete(f"/api/v1/experiences/{experience_id}/evidence/{evidence_id}")

    assert deleted.status_code == 200
    assert deleted.json()["evidence_ids"] == []
    assert deleted.json()["evidence_items"] == []
    async with isolated_db.session() as session:
        assert await EvidenceRepository(session).get(evidence_id) is None


async def test_reorder_evidence_requires_exact_unique_id_set_and_preserves_requested_order(isolated_db) -> None:
    """Accepting a partial, extra, or duplicated order would silently drop or duplicate evidence."""
    async with _client() as client:
        experience = await client.post("/api/v1/experiences", json={"title": "Order proof"})
        experience_id = experience.json()["experience_id"]
        first = await client.post(
            f"/api/v1/experiences/{experience_id}/evidence", json={"action": "First"}
        )
        first_id = first.json()["evidence_ids"][0]
        second = await client.post(
            f"/api/v1/experiences/{experience_id}/evidence", json={"action": "Second"}
        )
        second_id = second.json()["evidence_ids"][1]
        missing = await client.put(
            f"/api/v1/experiences/{experience_id}/evidence-order", json={"evidence_ids": [first_id]}
        )
        duplicate = await client.put(
            f"/api/v1/experiences/{experience_id}/evidence-order",
            json={"evidence_ids": [first_id, first_id]},
        )
        extra = await client.put(
            f"/api/v1/experiences/{experience_id}/evidence-order",
            json={"evidence_ids": [first_id, second_id, 99999]},
        )
        reordered = await client.put(
            f"/api/v1/experiences/{experience_id}/evidence-order",
            json={"evidence_ids": [second_id, first_id]},
        )

    assert [response.status_code for response in (missing, duplicate, extra)] == [422, 422, 422]
    assert reordered.status_code == 200
    assert reordered.json()["evidence_ids"] == [second_id, first_id]
    assert [item["id"] for item in reordered.json()["evidence_items"]] == [second_id, first_id]


async def test_evidence_mutations_recompute_completeness_and_downgrade_ready_experiences(isolated_db) -> None:
    """Skipping recomputation or ready-to-draft downgrade would advertise an incomplete record as ready."""
    create_payload = {
        "kind": "project",
        "title": "Complete enough",
        "organization": "Kestrel",
        "role": "Engineer",
        "start_date": "2026-01",
        "is_current": True,
        "background": "Built a useful product.",
    }
    async with _client() as client:
        experience = await client.post("/api/v1/experiences", json=create_payload)
        experience_id = experience.json()["experience_id"]
        enriched = await client.post(
            f"/api/v1/experiences/{experience_id}/evidence",
            json={"action": "Delivered", "result": "Released", "metrics": "40% faster"},
        )
        evidence_id = enriched.json()["evidence_ids"][0]

    assert enriched.json()["completeness"] == 100
    async with isolated_db.session() as session:
        await ExperienceRepository(session).set_status(experience_id, "ready")
        await session.commit()

    async with _client() as client:
        reduced = await client.delete(f"/api/v1/experiences/{experience_id}/evidence/{evidence_id}")

    assert reduced.status_code == 200
    assert reduced.json()["completeness"] == 55
    assert reduced.json()["status"] == "draft"


async def test_failed_evidence_delete_rolls_back_reference_and_row(isolated_db) -> None:
    """A failure after detaching evidence must not commit a dangling row or a missing reference."""
    async with _client() as client:
        experience = await client.post("/api/v1/experiences", json={"title": "Rollback proof"})
        experience_id = experience.json()["experience_id"]
        created = await client.post(
            f"/api/v1/experiences/{experience_id}/evidence", json={"action": "Must survive"}
        )
        evidence_id = created.json()["evidence_ids"][0]

    with patch(
        "app.services.evidence_service.EvidenceRepository.delete",
        new_callable=AsyncMock,
        side_effect=RuntimeError("forced delete failure"),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            failed = await client.delete(f"/api/v1/experiences/{experience_id}/evidence/{evidence_id}")

    assert failed.status_code == 500
    async with isolated_db.session() as session:
        stored_experience = await ExperienceRepository(session).get(experience_id)
        stored_evidence = await EvidenceRepository(session).get(evidence_id)
    assert stored_experience is not None
    assert stored_experience.evidence_ids == [evidence_id]
    assert stored_evidence is not None
    assert stored_evidence.action == "Must survive"


async def test_stale_evidence_mutation_becomes_a_domain_conflict(isolated_db, monkeypatch) -> None:
    """Leaking a stale repository write would turn an ordinary concurrent edit into a 500."""
    async with _client() as client:
        experience = await client.post("/api/v1/experiences", json={"title": "Stale proof"})
        experience_id = experience.json()["experience_id"]
        created = await client.post(
            f"/api/v1/experiences/{experience_id}/evidence", json={"action": "Original"}
        )
        evidence_id = created.json()["evidence_ids"][0]

    async with isolated_db.session() as session:
        service = EvidenceService(session)

        async def stale_claim(*_args, **_kwargs):
            raise ExperienceStaleWriteError("stale experience update")

        monkeypatch.setattr(service._experiences, "set_evidence_ids_if_current", stale_claim)
        with pytest.raises(ExperienceConflictError):
            await service.patch(evidence_id=evidence_id, experience_id=experience_id, request=ExperienceUpdate())

    async with isolated_db.session() as session:
        stored_evidence = await EvidenceRepository(session).get(evidence_id)
    assert stored_evidence is not None
    assert stored_evidence.action == "Original"


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
