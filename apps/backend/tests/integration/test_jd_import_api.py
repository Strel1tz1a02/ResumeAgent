"""JD import module API integration tests."""

from app.jd_import.models import JDInformation, JDOrigin, JDRequirement
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
                "raw_text": "Backend Engineer\nPython is required.",
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
    assert payload["status"] == "analysing"
    assert payload["revision"] == 0
    assert payload["origin"]["raw_text"].startswith("Backend Engineer")
    assert [item["revision"] for item in payload["requirements"]] == [0, 0]


async def test_requirement_mutations_advance_item_and_aggregate_revisions(
    isolated_db,
) -> None:
    async with _client() as client:
        created = await client.post(
            "/api/v1/jd-imports",
            json={"raw_text": "Python required", "job_name": "Engineer"},
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


async def test_confirmed_import_is_read_only_until_reopened(isolated_db) -> None:
    async with _client() as client:
        created = await client.post(
            "/api/v1/jd-imports",
            json={"raw_text": "Python required", "job_name": "Engineer"},
        )
        jd_id = created.json()["id"]
        confirmed = await client.patch(
            f"/api/v1/jd-imports/{jd_id}",
            json={"status": "confirmed", "expected_revision": 0},
        )
        blocked = await client.patch(
            f"/api/v1/jd-imports/{jd_id}",
            json={"company": "Changed", "expected_revision": 1},
        )
        reopened = await client.patch(
            f"/api/v1/jd-imports/{jd_id}",
            json={"status": "analysing", "expected_revision": 1},
        )
        changed = await client.patch(
            f"/api/v1/jd-imports/{jd_id}",
            json={"company": "Changed", "expected_revision": 2},
        )

    assert confirmed.json()["status"] == "confirmed"
    assert blocked.status_code == 409
    assert reopened.json()["status"] == "analysing"
    assert changed.json()["company"] == "Changed"
    assert changed.json()["revision"] == 3


async def test_delete_origin_cascades_whole_aggregate(isolated_db) -> None:
    async with _client() as client:
        created = await client.post(
            "/api/v1/jd-imports",
            json={
                "raw_text": "Python required",
                "requirements": [{"content": "Python", "priority": "required"}],
            },
        )
        payload = created.json()
        deleted = await client.delete(f"/api/v1/jd-imports/{payload['id']}")
        missing = await client.get(f"/api/v1/jd-imports/{payload['id']}")

    assert deleted.status_code == 204
    assert missing.status_code == 404
    async with isolated_db.session() as session:
        assert await session.scalar(select(func.count()).select_from(JDOrigin)) == 0
        assert await session.scalar(select(func.count()).select_from(JDInformation)) == 0
        assert await session.scalar(select(func.count()).select_from(JDRequirement)) == 0


async def test_rejects_invalid_enums_and_blank_content(isolated_db) -> None:
    async with _client() as client:
        invalid_status = await client.post(
            "/api/v1/jd-imports",
            json={"raw_text": "Valid JD", "status": "draft"},
        )
        blank_origin = await client.post(
            "/api/v1/jd-imports",
            json={"raw_text": "   "},
        )
        invalid_requirement = await client.post(
            "/api/v1/jd-imports",
            json={
                "raw_text": "Valid JD",
                "requirements": [{"content": "", "priority": "critical"}],
            },
        )

    assert invalid_status.status_code == 422
    assert blank_origin.status_code == 422
    assert invalid_requirement.status_code == 422
