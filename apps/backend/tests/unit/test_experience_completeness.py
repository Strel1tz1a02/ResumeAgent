"""Behavior tests for deterministic experience completeness scoring."""

from types import SimpleNamespace

from app.prompts.templates import get_language_name
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

    result = calculate_completeness(experience, [], language="en")

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
    assert result.suggested_questions[0] == "What concise title best describes this experience?"
    assert result.suggested_questions[-1].startswith("What measurable result")


def test_suggested_questions_use_the_configured_content_language() -> None:
    """Guidance must be user-facing copy, not internal keys or English-only fallback text."""
    experience = SimpleNamespace(
        kind="project",
        title="Project",
        organization=None,
        role="Developer",
        start_date="2026-01",
        end_date=None,
        is_current=True,
        background="Context",
    )

    chinese = calculate_completeness(experience, [], language="zh")
    english = calculate_completeness(experience, [], language="en")
    unknown = calculate_completeness(experience, [], language="unknown")

    assert chinese.suggested_questions[0] == "这段经历对应哪个组织、团队或客户？"
    assert english.suggested_questions[0] == "Which organization, team, or client was this experience with?"
    assert unknown.suggested_questions == chinese.suggested_questions
    assert "organization" not in chinese.suggested_questions


def test_every_supported_language_has_natural_guidance() -> None:
    experience = SimpleNamespace(
        kind="project",
        title="Project",
        organization=None,
        role="Developer",
        start_date="2026-01",
        end_date=None,
        is_current=True,
        background="Context",
    )

    questions = {
        language: calculate_completeness(experience, [], language=language).suggested_questions[0]
        for language in ("zh", "en")
    }

    assert len(set(questions.values())) == 2
    assert all(question != "organization" for question in questions.values())


def test_unknown_prompt_language_defaults_to_chinese() -> None:
    assert get_language_name("unknown") == "Chinese (Simplified)"


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
