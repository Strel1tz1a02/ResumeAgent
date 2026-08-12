"""Boundary-contract tests for experience-library schemas and ORM metadata."""

import pytest
from app.experience.schemas.evidence_items import (
    EvidenceCreate,
    EvidenceReorder,
    EvidenceUpdate,
)
from app.experience.schemas.experiences import (
    DeletionImpactResponse,
    ExperienceCompleteness,
    ExperienceCreate,
    ExperienceDetail,
    ExperienceGlobalSave,
    ExperienceKind,
    ExperienceRead,
    ExperienceStatus,
    ExperienceUpdate,
    ReadyConflictResponse,
)
from pydantic import ValidationError


def test_experience_kind_and_status_accept_only_stable_values() -> None:
    """Changing the enum contract must reject unsupported persisted categories."""
    created = ExperienceCreate(kind="research", title="Paper")

    assert created.kind is ExperienceKind.research
    assert ExperienceStatus.ready.value == "ready"
    with pytest.raises(ValidationError):
        ExperienceCreate(kind="employment", title="Engineer")
    with pytest.raises(ValidationError):
        ExperienceRead(
            experience_id=1,
            kind="work",
            title="Engineer",
            status="deleted",
            completeness=0,
            created_at="2026-07-01T00:00:00+00:00",
            updated_at="2026-07-01T00:00:00+00:00",
        )


def test_dates_require_valid_year_month_values() -> None:
    """Accepting invalid calendar months would leak malformed dates into storage."""
    created = ExperienceCreate(
        kind="work", title="Engineer", start_date="2026-07", end_date="2026-08"
    )

    assert created.start_date == "2026-07"
    with pytest.raises(ValidationError):
        ExperienceCreate(kind="work", title="Engineer", start_date="2026-13")
    with pytest.raises(ValidationError):
        ExperienceCreate(kind="work", title="Engineer", start_date="2026-7")


def test_current_experience_rejects_end_date() -> None:
    """A current item with an end date would represent contradictory employment facts."""
    with pytest.raises(ValidationError):
        ExperienceCreate(
            kind="work", title="Engineer", is_current=True, end_date="2026-07"
        )


def test_technology_and_tag_lists_are_normalized_and_deduplicated() -> None:
    """Duplicate or blank labels must not persist into library filtering metadata."""
    created = ExperienceCreate(
        kind="project",
        title="Agent",
        technologies=[" Python ", "python", "FastAPI", "", "FastAPI "],
        tags=[" AI ", "ai", "Career", "   "],
    )

    assert created.technologies == ["Python", "FastAPI"]
    assert created.tags == ["AI", "Career"]


def test_client_cannot_set_completeness() -> None:
    """Accepting server-owned scores would let clients bypass readiness controls."""
    with pytest.raises(ValidationError):
        ExperienceUpdate.model_validate({"title": "X", "completeness": 99})
    with pytest.raises(ValidationError):
        ExperienceCreate.model_validate(
            {"kind": "work", "title": "X", "completeness": 99}
        )


def test_evidence_action_must_not_be_blank() -> None:
    """Evidence without an action cannot support the action completeness dimension."""
    with pytest.raises(ValidationError):
        EvidenceCreate(action="   ")
    with pytest.raises(ValidationError):
        EvidenceUpdate(action="")


def test_evidence_reorder_rejects_duplicate_identifiers() -> None:
    """Duplicate references would violate the ordered unique evidence-ID invariant."""
    with pytest.raises(ValidationError):
        EvidenceReorder(evidence_ids=[7, 7])


def test_global_save_uses_ids_to_distinguish_create_from_overwrite() -> None:
    created = ExperienceGlobalSave.model_validate(
        {
            "experience": {"title": "New"},
            "evidence_items": [{"action": "First"}, {"action": "Second"}],
        }
    )
    updated = ExperienceGlobalSave.model_validate(
        {
            "experience_id": 7,
            "experience": {
                "title": "Updated",
                "expected_field_revisions": {"title": 2},
            },
            "evidence_items": [
                {"evidence_id": 3, "action": "Changed", "expected_revision": 4},
                {"action": "Appended"},
            ],
            "expected_collection_revision": 5,
        }
    )

    assert created.experience_id is None
    assert [item.evidence_id for item in created.evidence_items] == [None, None]
    assert updated.experience_id == 7
    assert [item.evidence_id for item in updated.evidence_items] == [3, None]


def test_global_save_rejects_incomplete_revision_pairs() -> None:
    with pytest.raises(ValidationError):
        ExperienceGlobalSave.model_validate(
            {
                "experience_id": 7,
                "experience": {"title": "Updated"},
                "evidence_items": [],
            }
        )
    with pytest.raises(ValidationError):
        ExperienceGlobalSave.model_validate(
            {
                "experience_id": 7,
                "experience": {"title": "Updated"},
                "evidence_items": [{"evidence_id": 3, "action": "Changed"}],
                "expected_collection_revision": 1,
            }
        )


def test_detail_and_conflict_responses_expose_derived_aggregates() -> None:
    """Omitting derived fields would leave clients unable to render authoritative guidance."""
    record = ExperienceRead(
        experience_id=1,
        kind="project",
        title="Agent",
        evidence_ids=[2],
        status="draft",
        completeness=65,
        created_at="2026-07-01T00:00:00+00:00",
        updated_at="2026-07-01T00:00:00+00:00",
    )
    detail = ExperienceDetail(
        **record.model_dump(),
        evidence_items=[
            {
                "id": 2,
                "action": "Built API",
                "created_at": "2026-07-01T00:00:00+00:00",
                "updated_at": "2026-07-01T00:00:00+00:00",
            }
        ],
        missing_dimensions=["evidence_background"],
        suggested_questions=["evidence background"],
    )

    assert detail.evidence_items[0].action == "Built API"
    assert ExperienceCompleteness(
        completeness=65,
        missing_dimensions=["evidence_background"],
        suggested_questions=["evidence background"],
    ).missing_dimensions == ["evidence_background"]
    assert (
        ReadyConflictResponse(
            completeness=65, missing_dimensions=["evidence_background"]
        ).completeness
        == 65
    )
    assert DeletionImpactResponse().affected_matches == []


def test_deletion_impact_uses_the_forward_compatible_match_shape() -> None:
    impact = DeletionImpactResponse(
        affected_matches=[{"match_id": 31, "job_title": "AI Engineer"}],
        affected_resumes=[],
    )

    assert impact.affected_matches[0].match_id == 31
