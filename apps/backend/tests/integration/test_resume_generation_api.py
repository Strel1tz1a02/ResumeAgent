"""简历生成 API 的确定性端到端闭环。"""

import importlib
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.experience.models import EvidenceItem, ExperienceEvidence, ExperienceItem
from app.jd_import.models import JDInformation, JDRequirement
from app.main import app
from app.models import Resume
from app.resume_generation.models import ResumeGenerationRun
from app.resume_generation.schemas import RetrievedEvidence
from app.resume_generation.service import ResumeGenerationService

resume_generation_router = importlib.import_module("app.resume_generation.router")


class _FakeRetriever:
    """API 测试隔离外部 Qdrant；原生查询契约由单元测试覆盖。"""

    async def retrieve(self, tasks, documents):
        return [
            RetrievedEvidence(
                document=document,
                retrieval_score=0.9,
                task_ids=[task.task_id for task in tasks],
            )
            for document in documents
        ]


@pytest.fixture(autouse=True)
def _replace_external_qdrant(monkeypatch: pytest.MonkeyPatch) -> None:
    def build_service(session: Any) -> ResumeGenerationService:
        return ResumeGenerationService(
            session,
            retriever=_FakeRetriever(),
        )

    monkeypatch.setattr(resume_generation_router, "_service", build_service)


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_sources(isolated_db) -> tuple[int, int, int]:
    async with isolated_db.session() as session:
        jd = JDInformation(
            company="Example",
            job_name="Backend Engineer",
            type="backend",
            location="Shanghai",
            status="confirmed",
            revision=0,
        )
        jd.requirements = [
            JDRequirement(
                priority="required",
                content="使用 Python 和 FastAPI 设计 API",
                sort_order=0,
                revision=0,
            )
        ]
        experience = ExperienceItem(
            kind="project",
            title="API Platform",
            organization="Personal",
            role="Developer",
            start_date="2025-01",
            end_date="2025-06",
            background="后端接口平台",
            technologies=["Python", "FastAPI"],
            tags=["API"],
            status="ready",
            completeness=90,
        )
        evidence = EvidenceItem(
            background="需要提供稳定接口",
            action="使用 Python 和 FastAPI 设计并实现 API",
            result="完成接口交付和参数校验",
        )
        session.add_all([jd, experience, evidence])
        await session.flush()
        session.add(
            ExperienceEvidence(
                experience_id=experience.experience_id,
                evidence_id=evidence.id,
                position=0,
            )
        )
        await session.commit()
        return jd.id, experience.experience_id, evidence.id


async def test_preview_get_and_confirm_are_grounded_and_idempotent(isolated_db) -> None:
    jd_id, experience_id, evidence_id = await _seed_sources(isolated_db)

    async with _client() as client:
        preview = await client.post(
            "/api/v1/resume-generations/preview",
            json={
                "jd_information_id": jd_id,
                "mode": "deterministic",
                "constraints": {"max_search_rounds": 2},
            },
        )
        assert preview.status_code == 201, preview.text
        payload = preview.json()
        run_id = payload["run_id"]
        fetched = await client.get(f"/api/v1/resume-generations/{run_id}")
        first_confirm = await client.post(
            f"/api/v1/resume-generations/{run_id}/confirm", json={}
        )
        second_confirm = await client.post(
            f"/api/v1/resume-generations/{run_id}/confirm", json={}
        )

    assert payload["validation"]["valid"] is True
    assert payload["plan"]["selected_experiences"][0]["experience_id"] == experience_id
    assert payload["provenance"]["bullets"][0]["evidence_ids"] == [evidence_id]
    assert fetched.status_code == 200
    assert fetched.json()["jd_snapshot"]["source"]["revision"] == 0
    assert "base_resume_id" not in fetched.json()
    assert "base_resume_id" not in fetched.json()["request"]
    assert first_confirm.status_code == 200
    assert second_confirm.status_code == 200
    assert first_confirm.json()["resume_id"] == second_confirm.json()["resume_id"]

    async with isolated_db.session() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ResumeGenerationRun))
            == 1
        )
        assert await session.scalar(select(func.count()).select_from(Resume)) == 1
        generated_resume = await session.get(Resume, first_confirm.json()["resume_id"])
        assert generated_resume is not None
        assert generated_resume.parent_id is None
        source = await session.get(JDInformation, jd_id)
        experience = await session.get(ExperienceItem, experience_id)
        assert source.revision == 0
        assert experience.status == "ready"


async def test_preview_rejects_missing_requirements_and_ready_experiences(
    isolated_db,
) -> None:
    async with isolated_db.session() as session:
        jd = JDInformation(
            company="Example",
            job_name="Engineer",
            type="",
            location="",
            status="incomplete",
            revision=0,
        )
        session.add(jd)
        await session.commit()
        jd_id = jd.id

    async with _client() as client:
        response = await client.post(
            "/api/v1/resume-generations/preview",
            json={"jd_information_id": jd_id, "mode": "deterministic"},
        )

    assert response.status_code == 422
    assert "requirement" in response.json()["detail"]


async def test_preview_rejects_legacy_base_resume_input(isolated_db) -> None:
    jd_id, _, _ = await _seed_sources(isolated_db)

    async with _client() as client:
        response = await client.post(
            "/api/v1/resume-generations/preview",
            json={
                "jd_information_id": jd_id,
                "base_resume_id": "master-1",
                "mode": "deterministic",
            },
        )

    assert response.status_code == 422


async def test_database_reset_removes_generation_runs(isolated_db) -> None:
    jd_id, _, _ = await _seed_sources(isolated_db)
    async with _client() as client:
        response = await client.post(
            "/api/v1/resume-generations/preview",
            json={"jd_information_id": jd_id, "mode": "deterministic"},
        )
    assert response.status_code == 201

    await isolated_db.reset_database()

    async with isolated_db.session() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ResumeGenerationRun))
            == 0
        )
