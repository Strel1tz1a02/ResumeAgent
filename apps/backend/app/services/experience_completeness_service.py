"""Pure, deterministic completeness scoring for experience-library records."""

from dataclasses import dataclass
from typing import Protocol, Sequence


READY_COMPLETENESS_THRESHOLD = 60

IDENTITY_POINTS = 10
ORGANIZATION_POINTS = 10
ROLE_POINTS = 10
DATES_POINTS = 10
BACKGROUND_POINTS = 15
ACTION_POINTS = 20
RESULT_POINTS = 15
METRICS_POINTS = 10

_PLACEHOLDER_TITLES = {"", "untitled experience"}


class ExperienceLike(Protocol):
    """The persisted experience fields consumed by completeness scoring."""

    kind: str
    title: str
    organization: str | None
    role: str | None
    start_date: str | None
    end_date: str | None
    is_current: bool
    background: str | None


class EvidenceLike(Protocol):
    """The evidence fields consumed by completeness scoring."""

    action: str
    result: str | None
    metrics: str | None


@dataclass(frozen=True)
class CompletenessResult:
    """A score plus stable, localization-neutral guidance keys."""

    completeness: int
    missing_dimensions: list[str]
    suggested_questions: list[str]


def _has_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_meaningful_title(title: object) -> bool:
    return _has_text(title) and title.strip().casefold() not in _PLACEHOLDER_TITLES


def calculate_completeness(
    experience: ExperienceLike,
    evidence_items: Sequence[EvidenceLike],
) -> CompletenessResult:
    """Score the eight factual dimensions without mutating persisted records."""
    dimensions = (
        (
            "identity",
            IDENTITY_POINTS,
            _has_text(experience.kind) and _has_meaningful_title(experience.title),
        ),
        ("organization", ORGANIZATION_POINTS, _has_text(experience.organization)),
        ("role", ROLE_POINTS, _has_text(experience.role)),
        (
            "dates",
            DATES_POINTS,
            _has_text(experience.start_date)
            and (_has_text(experience.end_date) or experience.is_current),
        ),
        ("background", BACKGROUND_POINTS, _has_text(experience.background)),
        ("action", ACTION_POINTS, any(_has_text(item.action) for item in evidence_items)),
        ("result", RESULT_POINTS, any(_has_text(item.result) for item in evidence_items)),
        ("metrics", METRICS_POINTS, any(_has_text(item.metrics) for item in evidence_items)),
    )
    missing_dimensions = [name for name, _, satisfied in dimensions if not satisfied]
    completeness = sum(points for _, points, satisfied in dimensions if satisfied)

    return CompletenessResult(
        completeness=max(0, min(100, completeness)),
        missing_dimensions=missing_dimensions,
        suggested_questions=missing_dimensions.copy(),
    )
