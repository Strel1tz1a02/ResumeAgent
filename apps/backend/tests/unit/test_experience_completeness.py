"""Behavior tests for deterministic experience completeness scoring."""

from types import SimpleNamespace

from app.services.experience_completeness_service import calculate_completeness


def test_complete_experience_scores_100() -> None:
    """Removing any scored fact must lower a fully evidenced experience score."""
    experience = SimpleNamespace(
        kind="project",
        title="Recruiting Assistant",
        organization="Personal project",
        role="Backend developer",
        start_date="2026-07",
        end_date="2026-08",
        is_current=False,
        background="Students needed organized job data",
    )
    evidence = [
        SimpleNamespace(
            action="Built APIs",
            result="Unified sources",
            metrics="5 sources",
        )
    ]

    result = calculate_completeness(experience, evidence)

    assert result.completeness == 100
    assert result.missing_dimensions == []
    assert result.suggested_questions == []


def test_placeholder_title_and_missing_facts_do_not_score() -> None:
    """Placeholder identity and absent facts must not be represented as complete."""
    experience = SimpleNamespace(
        kind="other",
        title="Untitled experience",
        organization=None,
        role=None,
        start_date=None,
        end_date=None,
        is_current=False,
        background=None,
    )

    result = calculate_completeness(experience, [])

    assert result.completeness == 0
    assert result.missing_dimensions == [
        "identity",
        "organization",
        "role",
        "dates",
        "background",
        "action",
        "result",
        "metrics",
    ]
    assert result.suggested_questions == [
        "identity",
        "organization",
        "role",
        "dates",
        "background",
        "action",
        "result",
        "metrics",
    ]


def test_evidence_dimensions_can_be_satisfied_by_different_rows() -> None:
    """Scoring must retain complementary evidence facts instead of requiring one full row."""
    experience = SimpleNamespace(
        kind="work",
        title="Engineer",
        organization="Acme",
        role="Developer",
        start_date="2026-01",
        end_date=None,
        is_current=True,
        background="Improve reliability",
    )
    evidence = [
        SimpleNamespace(action="Implemented monitoring", result=None, metrics=None),
        SimpleNamespace(action="", result="Reduced incidents", metrics="30% fewer"),
    ]

    result = calculate_completeness(experience, evidence)

    assert result.completeness == 100
    assert result.missing_dimensions == []
