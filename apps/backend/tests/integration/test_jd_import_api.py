"""JD import module API integration tests."""

from app.jd_import.models import JDInformation, JDRequirement
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select


def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


async def test_create_returns_new_independent_jd_aggregate(isolated_db) -> None:
    async with _client() as client:
        response = await client.post(
            "/api/v1/jd-imports",
            json={
                "source_url": "https://example.com/jobs/1",
                "company": "Example",
                "job_name": "Backend Engineer",
                "type": "backend",
                "location": "Shanghai",
                "requirements": [
                    {
                        "priority": "required",
                        "content": "Python",
                        "sort_order": 0,
                    },
                    {
                        "priority": "preferred",
                        "content": "FastAPI",
                        "sort_order": 1,
                    },
                ],
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "incomplete"
    assert payload["revision"] == 0
    assert payload["source_url"] == "https://example.com/jobs/1"
    assert "origin" not in payload
    assert [item["revision"] for item in payload["requirements"]] == [0, 0]


async def test_requirement_mutations_advance_item_and_aggregate_revisions(
    isolated_db,
) -> None:
    async with _client() as client:
        created = await client.post(
            "/api/v1/jd-imports",
            json={"job_name": "Engineer"},
        )
        jd_id = created.json()["id"]

        added = await client.post(
            f"/api/v1/jd-imports/{jd_id}/requirements",
            json={
                "priority": "required",
                "content": "Python",
                "sort_order": 0,
                "expected_information_revision": 0,
            },
        )
        requirement_id = added.json()["requirements"][0]["id"]

        updated = await client.patch(
            f"/api/v1/jd-imports/{jd_id}/requirements/{requirement_id}",
            json={
                "content": "Python 3",
                "expected_revision": 0,
                "expected_information_revision": 1,
            },
        )
        stale = await client.patch(
            f"/api/v1/jd-imports/{jd_id}/requirements/{requirement_id}",
            json={
                "content": "Stale overwrite",
                "expected_revision": 0,
                "expected_information_revision": 1,
            },
        )

    assert added.status_code == 201
    assert added.json()["revision"] == 1
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert updated.json()["requirements"][0]["revision"] == 1
    assert stale.status_code == 409


async def test_confirmed_import_remains_manually_editable(isolated_db) -> None:
    async with _client() as client:
        created = await client.post(
            "/api/v1/jd-imports",
            json={"job_name": "Engineer"},
        )
        jd_id = created.json()["id"]
        confirmed = await client.patch(
            f"/api/v1/jd-imports/{jd_id}",
            json={"status": "confirmed", "expected_revision": 0},
        )
        changed = await client.patch(
            f"/api/v1/jd-imports/{jd_id}",
            json={"company": "Changed", "expected_revision": 1},
        )

    assert confirmed.json()["status"] == "confirmed"
    assert changed.json()["company"] == "Changed"
    assert changed.json()["revision"] == 2


async def test_delete_information_cascades_requirements(isolated_db) -> None:
    async with _client() as client:
        created = await client.post(
            "/api/v1/jd-imports",
            json={
                "requirements": [{"content": "Python", "priority": "required"}],
            },
        )
        payload = created.json()
        deleted = await client.delete(f"/api/v1/jd-imports/{payload['id']}")
        missing = await client.get(f"/api/v1/jd-imports/{payload['id']}")

    assert deleted.status_code == 204
    assert missing.status_code == 404
    async with isolated_db.session() as session:
        assert await session.scalar(select(func.count()).select_from(JDInformation)) == 0
        assert await session.scalar(select(func.count()).select_from(JDRequirement)) == 0


async def test_rejects_invalid_enums_and_blank_requirement(isolated_db) -> None:
    async with _client() as client:
        invalid_status = await client.post(
            "/api/v1/jd-imports",
            json={"status": "analysing"},
        )
        invalid_requirement = await client.post(
            "/api/v1/jd-imports",
            json={
                "requirements": [{"content": "", "priority": "critical"}],
            },
        )

    assert invalid_status.status_code == 422
    assert invalid_requirement.status_code == 422


async def test_source_url_is_not_unique(isolated_db) -> None:
    async with _client() as client:
        first = await client.post(
            "/api/v1/jd-imports",
            json={"source_url": "https://example.com/jobs/1"},
        )
        second = await client.post(
            "/api/v1/jd-imports",
            json={"source_url": "https://example.com/jobs/1"},
        )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
