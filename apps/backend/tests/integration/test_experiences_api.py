"""个人经历库 API 的集成契约测试。"""

from unittest.mock import AsyncMock, patch

import pytest
from app.experience.repositories import (
    evidence_repository as evidence_repository_module,
)
from app.experience.repositories.evidence_repository import EvidenceRepository
from app.experience.repositories.experience_repository import (
    ExperienceRepository,
    ordered_evidence_ids,
)
from app.experience.schemas.evidence_items import EvidenceUpdate
from app.experience.schemas.experiences import ExperienceUpdate
from app.experience.services.evidence_service import EvidenceService
from app.experience.services.experience_field_service import FieldRevisionConflictError
from app.experience.services.experience_service import (
    ExperienceConflictError,
    ExperienceService,
)
from app.main import app
from app.models import Resume
from httpx import ASGITransport, AsyncClient


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _revision(payload: dict, key: str, ref_id: int | None = None) -> int:
    return next(
        state["revision"]
        for state in payload["field_states"]
        if state["key"] == key and state["ref_id"] == ref_id
    )


def _collection_revision(payload: dict) -> int:
    return _revision(payload, "evidence_new")


async def test_import_text_previews_without_writing_then_confirms(isolated_db) -> None:
    """解析只返回对象，用户确认后才持久化结构化内容。"""
    raw_text = "  Built a campus recruiting assistant.\n"
    parsed = {
        "experience": {
            "kind": "project",
            "title": "Recruiting assistant",
            "background": "Built a campus recruiting assistant.",
        },
        "evidence_items": [
            {"background": None, "action": "Built assistant", "result": None},
            {"background": "Needed review", "action": "Added preview", "result": None},
        ],
    }
    with patch(
        "app.experience.routers.experiences.ExperienceTextExtractor.extract",
        new=AsyncMock(return_value=parsed),
    ):
        async with _client() as client:
            preview = await client.post(
                "/api/v1/experiences/import-text/preview", json={"text": raw_text}
            )
            before_save = await client.get("/api/v1/experiences")
            response = await client.post(
                "/api/v1/experiences/save", json=preview.json()
            )

    assert preview.status_code == 200
    assert preview.json()["experience"]["title"] == "Recruiting assistant"
    assert before_save.json() == {"items": [], "total": 0}
    assert response.status_code == 200
    payload = response.json()
    assert "raw_input" not in payload
    assert payload["kind"] == "project"
    assert payload["title"] == "Recruiting assistant"
    assert payload["status"] == "draft"
    assert len(payload["evidence_ids"]) == 2
    assert payload["evidence_items"][0]["action"] == "Built assistant"
    assert payload["evidence_items"][1]["action"] == "Added preview"

    async with _client() as client:
        stored = await client.get(f"/api/v1/experiences/{payload['experience_id']}")
    assert stored.status_code == 200
    assert "raw_input" not in stored.json()


async def test_import_text_rejects_blank_and_oversized_requests(isolated_db) -> None:
    """移除导入文本长度限制或空值校验时，此契约必须失败。"""
    async with _client() as client:
        blank = await client.post(
            "/api/v1/experiences/import-text/preview", json={"text": " \n\t "}
        )
        oversized = await client.post(
            "/api/v1/experiences/import-text/preview", json={"text": "x" * 20_001}
        )

    assert blank.status_code == 422
    assert oversized.status_code == 422


async def test_manual_crud_list_search_and_detail_contract(isolated_db) -> None:
    """创建、更新、持久化、详情展开或查询转发被破坏时必须失败。"""
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
            json={
                "organization": "Campus Careers",
                "is_current": False,
                "end_date": "2026-07",
                "expected_field_revisions": {
                    "organization": _revision(payload, "organization"),
                    "is_current": _revision(payload, "is_current"),
                    "end_date": _revision(payload, "end_date"),
                },
            },
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


async def test_create_evidence_appends_it_and_returns_expanded_experience(
    isolated_db,
) -> None:
    """插入证据后必须建立关系归属并返回展开事实。"""
    async with _client() as client:
        created = await client.post(
            "/api/v1/experiences",
            json={"kind": "project", "title": "Evidence API"},
        )
        experience_id = created.json()["experience_id"]
        evidence = await client.post(
            f"/api/v1/experiences/{experience_id}/evidence",
            json={
                "background": "Needed an API",
                "action": "Built route",
                "result": "Returned expanded detail",
                "expected_collection_revision": _collection_revision(created.json()),
            },
        )

    assert evidence.status_code == 201
    payload = evidence.json()
    assert len(payload["evidence_ids"]) == 1
    assert payload["evidence_items"] == [
        {
            "id": payload["evidence_ids"][0],
            "background": "Needed an API",
            "action": "Built route",
            "result": "Returned expanded detail",
            "created_at": payload["evidence_items"][0]["created_at"],
            "updated_at": payload["evidence_items"][0]["updated_at"],
        }
    ]


async def test_patch_evidence_requires_ownership_and_hides_cross_experience_rows(
    isolated_db,
) -> None:
    """移除 JSON 成员校验会导致一条经历修改另一条经历的证据。"""
    async with _client() as client:
        first = await client.post("/api/v1/experiences", json={"title": "First"})
        second = await client.post("/api/v1/experiences", json={"title": "Second"})
        first_id = first.json()["experience_id"]
        second_id = second.json()["experience_id"]
        evidence = await client.post(
            f"/api/v1/experiences/{first_id}/evidence",
            json={
                "action": "Original",
                "expected_collection_revision": _collection_revision(first.json()),
            },
        )
        evidence_id = evidence.json()["evidence_ids"][0]
        denied = await client.patch(
            f"/api/v1/experiences/{second_id}/evidence/{evidence_id}",
            json={"action": "Stolen", "expected_revision": 0},
        )
        updated = await client.patch(
            f"/api/v1/experiences/{first_id}/evidence/{evidence_id}",
            json={
                "action": "Corrected",
                "result": "Saved",
                "expected_revision": _revision(evidence.json(), "action", evidence_id),
            },
        )

    assert denied.status_code == 404
    assert updated.status_code == 200
    assert updated.json()["evidence_items"][0]["action"] == "Corrected"
    assert updated.json()["evidence_items"][0]["result"] == "Saved"


async def test_delete_evidence_removes_row_and_json_reference_atomically(
    isolated_db,
) -> None:
    """遗留证据记录或其引用都会造成详情响应不一致。"""
    async with _client() as client:
        experience = await client.post(
            "/api/v1/experiences", json={"title": "Delete proof"}
        )
        experience_id = experience.json()["experience_id"]
        created = await client.post(
            f"/api/v1/experiences/{experience_id}/evidence",
            json={
                "action": "Disposable",
                "expected_collection_revision": _collection_revision(experience.json()),
            },
        )
        evidence_id = created.json()["evidence_ids"][0]
        deleted = await client.delete(
            f"/api/v1/experiences/{experience_id}/evidence/{evidence_id}",
            params={
                "expected_revision": _revision(created.json(), "action", evidence_id),
                "expected_collection_revision": _collection_revision(created.json()),
            },
        )

    assert deleted.status_code == 200
    assert deleted.json()["evidence_ids"] == []
    assert deleted.json()["evidence_items"] == []
    async with isolated_db.session() as session:
        assert await EvidenceRepository(session).get(evidence_id) is None


async def test_reorder_evidence_requires_exact_unique_id_set_and_preserves_requested_order(
    isolated_db,
) -> None:
    """接受缺失、多余或重复的顺序会静默丢失或复制证据。"""
    async with _client() as client:
        experience = await client.post(
            "/api/v1/experiences", json={"title": "Order proof"}
        )
        experience_id = experience.json()["experience_id"]
        first = await client.post(
            f"/api/v1/experiences/{experience_id}/evidence",
            json={
                "action": "First",
                "expected_collection_revision": _collection_revision(experience.json()),
            },
        )
        first_id = first.json()["evidence_ids"][0]
        second = await client.post(
            f"/api/v1/experiences/{experience_id}/evidence",
            json={
                "action": "Second",
                "expected_collection_revision": _collection_revision(first.json()),
            },
        )
        second_id = second.json()["evidence_ids"][1]
        missing = await client.put(
            f"/api/v1/experiences/{experience_id}/evidence-order",
            json={
                "evidence_ids": [first_id],
                "expected_collection_revision": _collection_revision(second.json()),
            },
        )
        duplicate = await client.put(
            f"/api/v1/experiences/{experience_id}/evidence-order",
            json={
                "evidence_ids": [first_id, first_id],
                "expected_collection_revision": _collection_revision(second.json()),
            },
        )
        extra = await client.put(
            f"/api/v1/experiences/{experience_id}/evidence-order",
            json={
                "evidence_ids": [first_id, second_id, 99999],
                "expected_collection_revision": _collection_revision(second.json()),
            },
        )
        reordered = await client.put(
            f"/api/v1/experiences/{experience_id}/evidence-order",
            json={
                "evidence_ids": [second_id, first_id],
                "expected_collection_revision": _collection_revision(second.json()),
            },
        )

    assert [response.status_code for response in (missing, duplicate, extra)] == [
        422,
        422,
        422,
    ]
    assert reordered.status_code == 200
    assert reordered.json()["evidence_ids"] == [second_id, first_id]
    assert [item["id"] for item in reordered.json()["evidence_items"]] == [
        second_id,
        first_id,
    ]


async def test_evidence_mutations_recompute_completeness_and_downgrade_ready_experiences(
    isolated_db,
) -> None:
    """跳过完整度重算或就绪降级会把不完整记录错误标记为就绪。"""
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
            json={
                "background": "Delivery was slow",
                "action": "Delivered",
                "result": "Released 40% faster",
                "expected_collection_revision": _collection_revision(experience.json()),
            },
        )
        evidence_id = enriched.json()["evidence_ids"][0]

    assert enriched.json()["completeness"] == 100
    async with isolated_db.session() as session:
        await ExperienceRepository(session).set_status(experience_id, "ready")
        await session.commit()

    async with _client() as client:
        reduced = await client.delete(
            f"/api/v1/experiences/{experience_id}/evidence/{evidence_id}",
            params={
                "expected_revision": _revision(enriched.json(), "action", evidence_id),
                "expected_collection_revision": _collection_revision(enriched.json()),
            },
        )

    assert reduced.status_code == 200
    assert reduced.json()["completeness"] == 55
    assert reduced.json()["status"] == "draft"


async def test_failed_evidence_delete_rolls_back_reference_and_row(isolated_db) -> None:
    """解除证据关联后发生失败时，不得提交悬空记录或缺失引用。"""
    async with _client() as client:
        experience = await client.post(
            "/api/v1/experiences", json={"title": "Rollback proof"}
        )
        experience_id = experience.json()["experience_id"]
        created = await client.post(
            f"/api/v1/experiences/{experience_id}/evidence",
            json={
                "action": "Must survive",
                "expected_collection_revision": _collection_revision(experience.json()),
            },
        )
        evidence_id = created.json()["evidence_ids"][0]

    with patch(
        "app.experience.services.evidence_service.EvidenceRepository.delete",
        new_callable=AsyncMock,
        side_effect=RuntimeError("forced delete failure"),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            failed = await client.delete(
                f"/api/v1/experiences/{experience_id}/evidence/{evidence_id}",
                params={
                    "expected_revision": _revision(
                        created.json(), "action", evidence_id
                    ),
                    "expected_collection_revision": _collection_revision(
                        created.json()
                    ),
                },
            )

    assert failed.status_code == 500
    async with isolated_db.session() as session:
        stored_experience = await ExperienceRepository(session).get(experience_id)
        stored_evidence = await EvidenceRepository(session).get(evidence_id)
    assert stored_experience is not None
    assert ordered_evidence_ids(stored_experience) == [evidence_id]
    assert stored_evidence is not None
    assert stored_evidence.action == "Must survive"


async def test_stale_evidence_mutation_becomes_a_domain_conflict(
    isolated_db, monkeypatch
) -> None:
    """泄漏仓储层陈旧写入异常会把普通并发编辑变成 500。"""
    async with _client() as client:
        experience = await client.post(
            "/api/v1/experiences", json={"title": "Stale proof"}
        )
        experience_id = experience.json()["experience_id"]
        created = await client.post(
            f"/api/v1/experiences/{experience_id}/evidence",
            json={
                "action": "Original",
                "expected_collection_revision": _collection_revision(experience.json()),
            },
        )
        evidence_id = created.json()["evidence_ids"][0]

    async with isolated_db.session() as session:
        service = EvidenceService(session)

        async def stale_claim(*_args, **_kwargs):
            raise FieldRevisionConflictError("stale revision")

        monkeypatch.setattr(service._fields, "claim_unit", stale_claim)
        with pytest.raises(ExperienceConflictError):
            await service.patch(
                evidence_id=evidence_id,
                experience_id=experience_id,
                request=EvidenceUpdate(action="Changed", expected_revision=0),
            )

    async with isolated_db.session() as session:
        stored_evidence = await EvidenceRepository(session).get(evidence_id)
    assert stored_evidence is not None
    assert stored_evidence.action == "Original"


async def test_patch_evidence_advances_its_timestamp_when_the_clock_regresses(
    isolated_db, monkeypatch
) -> None:
    """时钟冻结或回退不得削弱编辑后的证据审计顺序。"""
    async with _client() as client:
        experience = await client.post(
            "/api/v1/experiences", json={"title": "Audit proof"}
        )
        experience_id = experience.json()["experience_id"]
        created = await client.post(
            f"/api/v1/experiences/{experience_id}/evidence",
            json={
                "action": "Before",
                "expected_collection_revision": _collection_revision(experience.json()),
            },
        )
        evidence_id = created.json()["evidence_ids"][0]
        monkeypatch.setattr(
            evidence_repository_module,
            "_updated_at",
            lambda: "2000-01-01T00:00:00+00:00",
        )
        patched = await client.patch(
            f"/api/v1/experiences/{experience_id}/evidence/{evidence_id}",
            json={
                "action": "After",
                "expected_revision": _revision(created.json(), "action", evidence_id),
            },
        )

    assert patched.status_code == 200
    assert patched.json()["evidence_items"][0]["action"] == "After"
    assert (
        patched.json()["evidence_items"][0]["updated_at"] == "2000-01-01T00:00:00+00:00"
    )


async def test_missing_and_server_owned_fields_are_rejected(isolated_db) -> None:
    """允许缺失记录或客户端设置计算字段、生命周期字段时必须失败。"""
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


async def test_patch_rejects_current_and_end_date_conflicts_in_merged_state(
    isolated_db,
) -> None:
    """只校验稀疏更新字段会允许数据库保存相互矛盾的日期。"""
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
            json={
                "action": "Old action",
                "expected_collection_revision": _collection_revision(created.json()),
            },
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

        saved = await client.post(
            "/api/v1/experiences/save",
            json={
                "experience_id": experience_id,
                "experience": {
                    "title": "After",
                    "expected_field_revisions": {"title": revisions["title"]},
                },
                "evidence_items": [
                    {
                        "evidence_id": evidence_id,
                        "background": "Needed a release",
                        "action": "Updated action",
                        "result": "Released 20% faster",
                        "expected_revision": evidence_revision,
                    },
                    {"action": "Appended action"},
                ],
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


async def test_global_save_rolls_back_all_units_on_revision_conflict(
    isolated_db,
) -> None:
    """任一保存单元 revision 过期时不能留下部分主字段写入。"""
    async with _client() as client:
        created = await client.post(
            "/api/v1/experiences", json={"kind": "project", "title": "Stable"}
        )
        detail = created.json()
        experience_id = detail["experience_id"]
        conflict = await client.post(
            "/api/v1/experiences/save",
            json={
                "experience_id": experience_id,
                "experience": {
                    "title": "Must roll back",
                    "expected_field_revisions": {"title": 999},
                },
                "evidence_items": [],
                "expected_collection_revision": 0,
            },
        )
        stored = await client.get(f"/api/v1/experiences/{experience_id}")

    assert conflict.status_code == 409
    assert stored.json()["title"] == "Stable"


async def test_failed_import_rolls_back_its_uncommitted_record(isolated_db) -> None:
    """插入后失败时若不执行服务回滚，会留下幽灵草稿。"""
    with patch(
        "app.experience.services.experience_global_save_service.ExperienceRepository.set_completeness",
        new_callable=AsyncMock,
        side_effect=RuntimeError("score write failed"),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            failed = await client.post(
                "/api/v1/experiences/save",
                json={
                    "experience": {"kind": "other", "title": "Transient import"},
                    "evidence_items": [],
                },
            )

    assert failed.status_code == 500
    async with _client() as client:
        listed = await client.get("/api/v1/experiences")
    assert listed.status_code == 200
    assert listed.json() == {"items": [], "total": 0}


async def test_patch_rejects_null_for_non_nullable_persisted_fields(
    isolated_db,
) -> None:
    """向数据库非空列传递显式空值时不得演变为 500 错误。"""
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


async def test_patch_returns_conflict_without_overwriting_a_stale_winner(
    isolated_db,
) -> None:
    """同一保存单元的旧 revision 不能覆盖已经胜出的编辑。"""
    async with _client() as client:
        created = await client.post("/api/v1/experiences", json={"title": "Winner"})
        experience_id = created.json()["experience_id"]
        revision = _revision(created.json(), "title")
        winner = await client.patch(
            f"/api/v1/experiences/{experience_id}",
            json={
                "title": "Fresh winner",
                "expected_field_revisions": {"title": revision},
            },
        )
        stale = await client.patch(
            f"/api/v1/experiences/{experience_id}",
            json={
                "title": "Loser",
                "expected_field_revisions": {"title": revision},
            },
        )

    assert winner.status_code == 200
    assert stale.status_code == 409
    async with _client() as client:
        stored = await client.get(f"/api/v1/experiences/{experience_id}")
    assert stored.status_code == 200
    assert stored.json()["title"] == "Fresh winner"


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
            f"/api/v1/experiences/{experience_id}",
            json={
                "background": "New fact",
                "expected_field_revisions": {
                    "background": _revision(created.json(), "background")
                },
            },
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


async def test_mark_ready_rejects_incomplete_record_with_current_guidance(
    isolated_db,
) -> None:
    """移除就绪校验会让不完整草稿被错误标记为就绪。"""
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
            "evidence_background",
        ],
    }
    async with _client() as client:
        stored = await client.get(f"/api/v1/experiences/{experience_id}")
    assert stored.json()["status"] == "draft"


async def test_mark_ready_promotes_complete_draft_and_manual_edit_downgrades_it(
    isolated_db,
) -> None:
    """跳过就绪转换或低于阈值时的降级会遗留陈旧就绪状态。"""
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
            json={
                "background": "Delivery was slow",
                "action": "Built APIs",
                "result": "Released 40% faster",
                "expected_collection_revision": _collection_revision(created.json()),
            },
        )
        ready = await client.post(f"/api/v1/experiences/{experience_id}/mark-ready")
        reduced = await client.patch(
            f"/api/v1/experiences/{experience_id}",
            json={
                "organization": None,
                "role": None,
                "start_date": None,
                "background": None,
                "expected_field_revisions": {
                    "organization": _revision(ready.json(), "organization"),
                    "role": _revision(ready.json(), "role"),
                    "start_date": _revision(ready.json(), "start_date"),
                    "background": _revision(ready.json(), "background"),
                },
            },
        )

    assert enriched.json()["completeness"] == 100
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert reduced.status_code == 200
    assert reduced.json()["completeness"] == 55
    assert reduced.json()["status"] == "draft"


async def test_archive_restore_and_list_filters_keep_lifecycle_views_separate(
    isolated_db,
) -> None:
    """生命周期筛选回归可能让已归档记录出现在活动经历库中。"""
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
            json={
                "background": "Needed a launch",
                "action": "Built it",
                "result": "Released once",
                "expected_collection_revision": _collection_revision(first.json()),
            },
        )
        marked_ready = await client.post(f"/api/v1/experiences/{first_id}/mark-ready")
        archived = await client.post(f"/api/v1/experiences/{first_id}/archive")
        active = await client.get("/api/v1/experiences")
        archived_list = await client.get(
            "/api/v1/experiences", params={"status": "archived"}
        )
        search = await client.get(
            "/api/v1/experiences",
            params={"q": "python", "kind": "project", "status": "archived"},
        )
        ascending = await client.get(
            "/api/v1/experiences",
            params={"status": "archived", "sort": "created_at_asc"},
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


async def test_permanent_delete_requires_archive_and_preserves_unrelated_rows(
    isolated_db,
) -> None:
    """归档前删除或改动无关证据、简历会违反删除安全约束。"""
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
            f"/api/v1/experiences/{target_id}/evidence",
            json={
                "action": "Disposable",
                "expected_collection_revision": _collection_revision(target.json()),
            },
        )
        other_evidence = await client.post(
            f"/api/v1/experiences/{other_id}/evidence",
            json={
                "action": "Keep evidence",
                "expected_collection_revision": _collection_revision(other.json()),
            },
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
    """完整度刷新失败时，不得让已刷新的标题保持可见或被提交。"""
    async with _client() as client:
        created = await client.post("/api/v1/experiences", json={"title": "Original"})
    experience_id = created.json()["experience_id"]

    async with isolated_db.session() as session:
        service = ExperienceService(session)

        async def fail_after_write(*_args, **_kwargs):
            raise RuntimeError("forced completeness failure")

        monkeypatch.setattr(
            service._experienceRepository, "set_completeness", fail_after_write
        )
        with pytest.raises(RuntimeError, match="forced completeness failure"):
            await service.patch(
                experience_id,
                ExperienceUpdate(
                    title="Uncommitted",
                    expected_field_revisions={
                        "title": _revision(created.json(), "title")
                    },
                ),
            )

        assert not session.in_transaction()
        assert (await service.get(experience_id)).title == "Original"

    async with isolated_db.session() as reopened_session:
        assert (
            await ExperienceService(reopened_session).get(experience_id)
        ).title == "Original"


async def test_patch_maps_post_write_value_error_to_422_and_rolls_back(
    isolated_db,
) -> None:
    """标题写入后若泄漏仓储 ValueError，会把校验错误变成 500。"""
    async with _client() as client:
        created = await client.post("/api/v1/experiences", json={"title": "Original"})
        experience_id = created.json()["experience_id"]

    with patch(
        "app.experience.services.experience_service.ExperienceRepository.set_completeness",
        new_callable=AsyncMock,
        side_effect=ValueError("forced completeness validation"),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            failed = await client.patch(
                f"/api/v1/experiences/{experience_id}",
                json={
                    "title": "Uncommitted",
                    "expected_field_revisions": {
                        "title": _revision(created.json(), "title")
                    },
                },
            )

    assert failed.status_code == 422
    async with _client() as client:
        stored = await client.get(f"/api/v1/experiences/{experience_id}")
    assert stored.json()["title"] == "Original"


async def test_restore_rejects_active_draft_and_ready_experiences(isolated_db) -> None:
    """允许非归档状态执行恢复会破坏生命周期操作的幂等性。"""
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
            json={
                "background": "Needed a launch",
                "action": "Built",
                "result": "Released once",
                "expected_collection_revision": _collection_revision(ready.json()),
            },
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
