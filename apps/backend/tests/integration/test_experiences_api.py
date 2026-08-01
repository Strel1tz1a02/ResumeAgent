"""Integration contracts for the person-level experience library API."""

from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
import pytest

from app.database import Database
from app.main import app
from app.models import ExperienceItem, Resume
from app.repositories import evidence_repository as evidence_repository_module
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.experience_repository import ExperienceRepository
from app.repositories.experience_repository import ExperienceStaleWriteError
from app.schemas.evidence_items import EvidenceCreate
from app.schemas.experiences import ExperienceCreate, ExperienceUpdate
from app.services.evidence_service import EvidenceService
from app.services.experience_service import ExperienceConflictError, ExperienceService


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_import_text_persists_only_structured_llm_output(isolated_db) -> None:
    """导入原文只用于解析，持久化响应不得包含原始文本。"""
    raw_text = "  Built a campus recruiting assistant.\n"
    parsed = {
        "kind": "project",
        "title": "Recruiting assistant",
        "background": "Built a campus recruiting assistant.",
        "evidence_items": [{"action": "Built assistant", "result": None, "metrics": None}],
    }
    with patch(
        "app.services.experience_import_service.complete_json",
        new=AsyncMock(return_value=parsed),
    ):
        async with _client() as client:
            response = await client.post(
                "/api/v1/experiences/import-text", json={"text": raw_text}
            )

    assert response.status_code == 201
    payload = response.json()
    assert "raw_input" not in payload
    assert payload["kind"] == "project"
    assert payload["title"] == "Recruiting assistant"
    assert payload["status"] == "draft"
    assert len(payload["evidence_ids"]) == 1
    assert payload["evidence_items"][0]["action"] == "Built assistant"

    async with _client() as client:
        stored = await client.get(f"/api/v1/experiences/{payload['experience_id']}")
    assert stored.status_code == 200
    assert "raw_input" not in stored.json()


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


async def test_patch_evidence_advances_its_timestamp_when_the_clock_regresses(
    isolated_db, monkeypatch
) -> None:
    """A frozen or regressed clock must not weaken evidence audit ordering after an edit."""
    async with _client() as client:
        experience = await client.post("/api/v1/experiences", json={"title": "Audit proof"})
        experience_id = experience.json()["experience_id"]
        created = await client.post(
            f"/api/v1/experiences/{experience_id}/evidence", json={"action": "Before"}
        )
        evidence_id = created.json()["evidence_ids"][0]
        original_updated_at = created.json()["evidence_items"][0]["updated_at"]

        monkeypatch.setattr(evidence_repository_module, "_updated_at", lambda: "2000-01-01T00:00:00+00:00")
        patched = await client.patch(
            f"/api/v1/experiences/{experience_id}/evidence/{evidence_id}",
            json={"action": "After"},
        )

    assert patched.status_code == 200
    assert patched.json()["evidence_items"][0]["action"] == "After"
    assert patched.json()["evidence_items"][0]["updated_at"] > original_updated_at


async def test_evidence_service_acquires_ownership_lock_before_loading_experience(
    isolated_db, monkeypatch
) -> None:
    """Reading ownership before the write lock would leave a cross-session assignment race."""
    async with _client() as client:
        created = await client.post("/api/v1/experiences", json={"title": "Lock order"})
        experience_id = created.json()["experience_id"]

    async with isolated_db.session() as session:
        service = EvidenceService(session)
        lock_acquired = False

        async def acquire_lock() -> None:
            nonlocal lock_acquired
            lock_acquired = True

        original_get = service._experiences.get

        async def guarded_get(experience_id: int):
            assert lock_acquired
            return await original_get(experience_id)

        monkeypatch.setattr(
            service._experiences, "acquire_ownership_write_lock", acquire_lock, raising=False
        )
        monkeypatch.setattr(service._experiences, "get", guarded_get)
        detail = await service.create(experience_id, EvidenceCreate(action="Locked first"))

    assert detail.evidence_items[0].action == "Locked first"


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


async def test_global_save_updates_all_units_in_one_transaction(isolated_db) -> None:
    """全局保存必须同时写入主字段、现有 Evidence 和待追加 Evidence。"""
    async with _client() as client:
        created = await client.post(
            "/api/v1/experiences", json={"kind": "project", "title": "Before"}
        )
        experience_id = created.json()["experience_id"]
        with_evidence = await client.post(
            f"/api/v1/experiences/{experience_id}/evidence",
            json={"action": "Old action"},
        )
        detail = with_evidence.json()
        evidence_id = detail["evidence_ids"][0]
        revisions = {
            state["key"]: state["revision"]
            for state in detail["field_states"]
            if state["ref_id"] is None
        }
        evidence_revision = next(
            state["revision"]
            for state in detail["field_states"]
            if state["key"] == "action" and state["ref_id"] == evidence_id
        )

        saved = await client.put(
            f"/api/v1/experiences/{experience_id}/save",
            json={
                "experience": {
                    "title": "After",
                    "expected_field_revisions": {"title": revisions["title"]},
                },
                "evidence_items": [
                    {
                        "evidence_id": evidence_id,
                        "action": "Updated action",
                        "result": "Released",
                        "metrics": "20%",
                        "expected_revision": evidence_revision,
                    }
                ],
                "new_evidence": {"action": "Appended action"},
                "expected_collection_revision": revisions["evidence_new"],
            },
        )

    assert saved.status_code == 200
    payload = saved.json()
    assert payload["title"] == "After"
    assert [item["action"] for item in payload["evidence_items"]] == [
        "Updated action",
        "Appended action",
    ]


async def test_global_save_rolls_back_all_units_on_revision_conflict(isolated_db) -> None:
    """任一保存单元 revision 过期时不能留下部分主字段写入。"""
    async with _client() as client:
        created = await client.post(
            "/api/v1/experiences", json={"kind": "project", "title": "Stable"}
        )
        detail = created.json()
        experience_id = detail["experience_id"]
        conflict = await client.put(
            f"/api/v1/experiences/{experience_id}/save",
            json={
                "experience": {
                    "title": "Must roll back",
                    "expected_field_revisions": {"title": 999},
                },
                "evidence_items": [],
                "new_evidence": None,
                "expected_collection_revision": 0,
            },
        )
        stored = await client.get(f"/api/v1/experiences/{experience_id}")

    assert conflict.status_code == 409
    assert stored.json()["title"] == "Stable"


async def test_failed_import_rolls_back_its_uncommitted_record(isolated_db) -> None:
    """Dropping service rollback after a post-insert failure would leave a ghost draft."""
    with patch(
        "app.services.experience_import_service.ExperienceRepository.set_completeness",
        new_callable=AsyncMock,
        side_effect=RuntimeError("score write failed"),
    ), patch(
        "app.services.experience_import_service.complete_json",
        new=AsyncMock(return_value={"kind": "other", "title": "Transient import"}),
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
            json={"kind": "project", "title": "Stable"},
        )
        experience_id = created.json()["experience_id"]

    responses = []
    for payload in (
        {"kind": None},
        {"title": None},
        {"is_current": None},
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

    assert [response.status_code for response in responses] == [422] * 5
    async with _client() as client:
        detail = await client.get(f"/api/v1/experiences/{experience_id}")
    assert detail.json()["kind"] == "project"
    assert detail.json()["title"] == "Stable"


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


async def test_patch_allows_disjoint_fields_after_another_field_changes(
    isolated_db,
) -> None:
    """字段级 revision 允许不同保存单元并发落库。"""
    async with _client() as client:
        created = await client.post("/api/v1/experiences", json={"title": "Original"})
        experience_id = created.json()["experience_id"]
        title_revision = next(
            state["revision"]
            for state in created.json()["field_states"]
            if state["key"] == "title" and state["ref_id"] is None
        )
        winner = await client.patch(
            f"/api/v1/experiences/{experience_id}", json={"background": "New fact"}
        )
        stale = await client.patch(
            f"/api/v1/experiences/{experience_id}",
            json={
                "title": "Independent edit",
                "expected_field_revisions": {"title": title_revision},
            },
        )
        stored = await client.get(f"/api/v1/experiences/{experience_id}")

    assert winner.status_code == 200
    assert stale.status_code == 200
    assert stored.json()["title"] == "Independent edit"
    assert stored.json()["background"] == "New fact"


async def test_patch_repairs_historical_missing_evidence_references(isolated_db) -> None:
    """A tolerated dangling JSON ID must not survive the next ordinary write."""
    async with _client() as client:
        created = await client.post("/api/v1/experiences", json={"title": "Before"})
    experience_id = created.json()["experience_id"]
    async with isolated_db.session() as session:
        item = await session.get(ExperienceItem, experience_id)
        assert item is not None
        item.evidence_ids = [999]
        await session.commit()

    async with _client() as client:
        patched = await client.patch(
            f"/api/v1/experiences/{experience_id}", json={"title": "After"}
        )

    assert patched.status_code == 200
    assert patched.json()["evidence_ids"] == []
    async with isolated_db.session() as session:
        stored = await session.get(ExperienceItem, experience_id)
        assert stored is not None
        assert stored.evidence_ids == []


async def test_lifecycle_write_repairs_historical_missing_evidence_references(isolated_db) -> None:
    async with _client() as client:
        created = await client.post("/api/v1/experiences", json={"title": "Archive"})
    experience_id = created.json()["experience_id"]
    async with isolated_db.session() as session:
        item = await session.get(ExperienceItem, experience_id)
        assert item is not None
        item.evidence_ids = [999]
        await session.commit()

    async with _client() as client:
        archived = await client.post(f"/api/v1/experiences/{experience_id}/archive")

    assert archived.status_code == 200
    assert archived.json()["evidence_ids"] == []


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


async def test_mark_ready_rejects_incomplete_record_with_current_guidance(isolated_db) -> None:
    """Removing readiness validation would let incomplete drafts be advertised as ready."""
    async with _client() as client:
        created = await client.post("/api/v1/experiences", json={"title": "Incomplete"})
        experience_id = created.json()["experience_id"]
        ready = await client.post(f"/api/v1/experiences/{experience_id}/mark-ready")

    assert ready.status_code == 409
    assert ready.json() == {
        "completeness": 10,
        "missing_dimensions": [
            "organization",
            "role",
            "dates",
            "background",
            "action",
            "result",
            "metrics",
        ],
    }
    async with _client() as client:
        stored = await client.get(f"/api/v1/experiences/{experience_id}")
    assert stored.json()["status"] == "draft"


async def test_mark_ready_promotes_complete_draft_and_manual_edit_downgrades_it(isolated_db) -> None:
    """Skipping the readiness transition or its below-threshold downgrade leaves stale ready state."""
    payload = {
        "kind": "project",
        "title": "Ready project",
        "organization": "Campus Lab",
        "role": "Engineer",
        "start_date": "2026-01",
        "is_current": True,
        "background": "Built the matching service.",
    }
    async with _client() as client:
        created = await client.post("/api/v1/experiences", json=payload)
        experience_id = created.json()["experience_id"]
        enriched = await client.post(
            f"/api/v1/experiences/{experience_id}/evidence",
            json={"action": "Built APIs", "result": "Released", "metrics": "40% faster"},
        )
        ready = await client.post(f"/api/v1/experiences/{experience_id}/mark-ready")
        reduced = await client.patch(
            f"/api/v1/experiences/{experience_id}",
            json={
                "organization": None,
                "role": None,
                "start_date": None,
                "background": None,
            },
        )

    assert enriched.json()["completeness"] == 100
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert reduced.status_code == 200
    assert reduced.json()["completeness"] == 55
    assert reduced.json()["status"] == "draft"


async def test_archive_restore_and_list_filters_keep_lifecycle_views_separate(isolated_db) -> None:
    """A lifecycle filter regression could surface archived records in the active library."""
    async with _client() as client:
        first = await client.post(
            "/api/v1/experiences",
            json={
                "kind": "project",
                "title": "Alpha",
                "organization": "Campus Lab",
                "role": "Engineer",
                "start_date": "2026-01",
                "is_current": True,
                "background": "Built the archive test project.",
                "tags": ["Python"],
            },
        )
        second = await client.post(
            "/api/v1/experiences",
            json={"kind": "work", "title": "Bravo", "organization": "Acme"},
        )
        first_id = first.json()["experience_id"]
        second_id = second.json()["experience_id"]
        await client.post(
            f"/api/v1/experiences/{first_id}/evidence",
            json={"action": "Built it", "result": "Released", "metrics": "1 launch"},
        )
        marked_ready = await client.post(f"/api/v1/experiences/{first_id}/mark-ready")
        archived = await client.post(f"/api/v1/experiences/{first_id}/archive")
        active = await client.get("/api/v1/experiences")
        archived_list = await client.get("/api/v1/experiences", params={"status": "archived"})
        search = await client.get(
            "/api/v1/experiences",
            params={"q": "python", "kind": "project", "status": "archived"},
        )
        ascending = await client.get(
            "/api/v1/experiences", params={"status": "archived", "sort": "created_at_asc"}
        )
        restored = await client.post(f"/api/v1/experiences/{first_id}/restore")

    assert marked_ready.json()["status"] == "ready"
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert archived.json()["archived_at"] is not None
    assert active.json()["total"] == 1
    assert active.json()["items"][0]["experience_id"] == second_id
    assert archived_list.json()["items"][0]["experience_id"] == first_id
    assert search.json()["items"][0]["experience_id"] == first_id
    assert ascending.json()["items"][0]["experience_id"] == first_id
    assert restored.status_code == 200
    assert restored.json()["status"] == "draft"
    assert restored.json()["archived_at"] is None


async def test_permanent_delete_requires_archive_and_preserves_unrelated_rows(isolated_db) -> None:
    """Deleting before archive or touching unrelated evidence/resumes would violate deletion safety."""
    async with isolated_db.session() as session:
        session.add(
            Resume(
                resume_id="seeded-resume",
                content="Seeded resume remains independent.",
                content_type="md",
            )
        )
        await session.commit()

    async with _client() as client:
        target = await client.post("/api/v1/experiences", json={"title": "Delete me"})
        other = await client.post("/api/v1/experiences", json={"title": "Keep me"})
        target_id = target.json()["experience_id"]
        other_id = other.json()["experience_id"]
        target_evidence = await client.post(
            f"/api/v1/experiences/{target_id}/evidence", json={"action": "Disposable"}
        )
        other_evidence = await client.post(
            f"/api/v1/experiences/{other_id}/evidence", json={"action": "Keep evidence"}
        )
        target_evidence_id = target_evidence.json()["evidence_ids"][0]
        other_evidence_id = other_evidence.json()["evidence_ids"][0]
        impact = await client.get(f"/api/v1/experiences/{target_id}/deletion-impact")
        rejected = await client.delete(f"/api/v1/experiences/{target_id}/permanent")
        await client.post(f"/api/v1/experiences/{target_id}/archive")
        deleted = await client.delete(f"/api/v1/experiences/{target_id}/permanent")

    assert impact.status_code == 200
    assert impact.json() == {"affected_matches": [], "affected_resumes": []}
    assert rejected.status_code == 409
    assert deleted.status_code == 204
    async with isolated_db.session() as session:
        assert await ExperienceRepository(session).get(target_id) is None
        assert await EvidenceRepository(session).get(target_evidence_id) is None
        assert await ExperienceRepository(session).get(other_id) is not None
        assert await EvidenceRepository(session).get(other_evidence_id) is not None
        assert await session.get(Resume, "seeded-resume") is not None


async def test_patch_rolls_back_post_write_failure_in_current_and_reopened_sessions(
    isolated_db, monkeypatch
) -> None:
    """A failed completeness refresh must not leave the already-flushed title visible or committed."""
    async with _client() as client:
        created = await client.post("/api/v1/experiences", json={"title": "Original"})
    experience_id = created.json()["experience_id"]

    async with isolated_db.session() as session:
        service = ExperienceService(session)

        async def fail_after_write(*_args, **_kwargs):
            raise RuntimeError("forced completeness failure")

        monkeypatch.setattr(service._experiences, "set_completeness", fail_after_write)
        with pytest.raises(RuntimeError, match="forced completeness failure"):
            await service.patch(experience_id, ExperienceUpdate(title="Uncommitted"))

        assert not session.in_transaction()
        assert (await service.get(experience_id)).title == "Original"

    async with isolated_db.session() as reopened_session:
        assert (await ExperienceService(reopened_session).get(experience_id)).title == "Original"


async def test_patch_maps_post_write_value_error_to_422_and_rolls_back(isolated_db) -> None:
    """Leaking a repository ValueError after the title write would turn validation into a 500."""
    async with _client() as client:
        created = await client.post("/api/v1/experiences", json={"title": "Original"})
        experience_id = created.json()["experience_id"]

    with patch(
        "app.services.experience_service.ExperienceRepository.set_completeness",
        new_callable=AsyncMock,
        side_effect=ValueError("forced completeness validation"),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            failed = await client.patch(
                f"/api/v1/experiences/{experience_id}", json={"title": "Uncommitted"}
            )

    assert failed.status_code == 422
    async with _client() as client:
        stored = await client.get(f"/api/v1/experiences/{experience_id}")
    assert stored.json()["title"] == "Original"


async def test_restore_rejects_active_draft_and_ready_experiences(isolated_db) -> None:
    """Allowing restore outside the archive state would make lifecycle actions non-idempotent."""
    ready_payload = {
        "kind": "project",
        "title": "Ready",
        "organization": "Campus Lab",
        "role": "Engineer",
        "start_date": "2026-01",
        "is_current": True,
        "background": "Built a complete project.",
    }
    async with _client() as client:
        draft = await client.post("/api/v1/experiences", json={"title": "Draft"})
        ready = await client.post("/api/v1/experiences", json=ready_payload)
        draft_id = draft.json()["experience_id"]
        ready_id = ready.json()["experience_id"]
        await client.post(
            f"/api/v1/experiences/{ready_id}/evidence",
            json={"action": "Built", "result": "Released", "metrics": "1 launch"},
        )
        marked_ready = await client.post(f"/api/v1/experiences/{ready_id}/mark-ready")
        draft_restore = await client.post(f"/api/v1/experiences/{draft_id}/restore")
        ready_restore = await client.post(f"/api/v1/experiences/{ready_id}/restore")
        stored_draft = await client.get(f"/api/v1/experiences/{draft_id}")
        stored_ready = await client.get(f"/api/v1/experiences/{ready_id}")

    assert marked_ready.json()["status"] == "ready"
    assert draft_restore.status_code == 409
    assert ready_restore.status_code == 409
    assert stored_draft.json()["status"] == "draft"
    assert stored_ready.json()["status"] == "ready"
