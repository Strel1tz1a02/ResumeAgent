"""经历库记录的纯函数、确定性完整度评分。"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

READY_COMPLETENESS_THRESHOLD = 60

IDENTITY_POINTS = 10
ORGANIZATION_POINTS = 10
ROLE_POINTS = 10
DATES_POINTS = 10
BACKGROUND_POINTS = 15
ACTION_POINTS = 20
RESULT_POINTS = 15
EVIDENCE_BACKGROUND_POINTS = 10

_PLACEHOLDER_TITLES = {"", "untitled experience"}

_QUESTIONS: dict[str, dict[str, str]] = {
    "en": {
        "identity": "What concise title best describes this experience?",
        "organization": "Which organization, team, or client was this experience with?",
        "role": "What was your specific role or primary responsibility?",
        "dates": "When did this experience start and end (YYYY-MM), or is it current?",
        "background": "What problem, context, or goal did this work address?",
        "action": "What specifically did you do?",
        "result": "What outcome resulted from your work?",
        "evidence_background": "What context, problem, or goal led to this specific action?",
    },
    "zh": {
        "identity": "哪一个简洁标题最能概括这段经历？",
        "organization": "这段经历对应哪个组织、团队或客户？",
        "role": "你在这段经历中承担的具体角色或主要职责是什么？",
        "dates": "这段经历何时开始和结束（YYYY-MM），还是仍在进行？",
        "background": "这项工作要解决什么问题，背景或目标是什么？",
        "action": "你具体采取了哪些行动？",
        "result": "你的行动产生了什么结果？",
        "evidence_background": "这项具体行动对应什么背景、问题或目标？",
    },
}


class ExperienceLike(Protocol):
    """完整度评分使用的已持久化经历字段。"""

    kind: str
    title: str
    organization: str | None
    role: str | None
    start_date: str | None
    end_date: str | None
    is_current: bool
    background: str | None


class EvidenceLike(Protocol):
    """完整度评分使用的证据字段。"""

    background: str | None
    action: str
    result: str | None


@dataclass(frozen=True)
class CompletenessResult:
    """分数以及稳定、与本地化无关的引导键。"""

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
    *,
    language: str = "zh",
) -> CompletenessResult:
    """对八个事实维度评分，不修改持久化记录。"""
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
        (
            "action",
            ACTION_POINTS,
            any(_has_text(item.action) for item in evidence_items),
        ),
        (
            "result",
            RESULT_POINTS,
            any(_has_text(item.result) for item in evidence_items),
        ),
        (
            "evidence_background",
            EVIDENCE_BACKGROUND_POINTS,
            any(_has_text(item.background) for item in evidence_items),
        ),
    )
    missing_dimensions = [name for name, _, satisfied in dimensions if not satisfied]
    completeness = sum(points for _, points, satisfied in dimensions if satisfied)

    questions = _QUESTIONS.get(language, _QUESTIONS["zh"])
    return CompletenessResult(
        completeness=max(0, min(100, completeness)),
        missing_dimensions=missing_dimensions,
        suggested_questions=[questions[dimension] for dimension in missing_dimensions],
    )


def question_for_dimension(dimension: str, language: str = "zh") -> str:
    """为一个缺失维度返回确定性的本地化引导。"""
    questions = _QUESTIONS.get(language, _QUESTIONS["zh"])
    return questions.get(
        dimension,
        {
            "zh": "还有哪些可核实的事实能让这段经历更清晰？",
            "en": "What additional factual detail would make this experience clearer?",
        }.get(language, "还有哪些可核实的事实能让这段经历更清晰？"),
    )
