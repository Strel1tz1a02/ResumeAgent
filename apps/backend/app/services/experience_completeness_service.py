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

_QUESTIONS: dict[str, dict[str, str]] = {
    "en": {
        "identity": "What concise title best describes this experience?",
        "organization": "Which organization, team, or client was this experience with?",
        "role": "What was your specific role or primary responsibility?",
        "dates": "When did this experience start and end (YYYY-MM), or is it current?",
        "background": "What problem, context, or goal did this work address?",
        "action": "What specifically did you do?",
        "result": "What outcome resulted from your work?",
        "metrics": "What measurable result can you confirm, such as a percentage, count, time, cost, or scale?",
    },
    "zh": {
        "identity": "哪一个简洁标题最能概括这段经历？",
        "organization": "这段经历对应哪个组织、团队或客户？",
        "role": "你在这段经历中承担的具体角色或主要职责是什么？",
        "dates": "这段经历何时开始和结束（YYYY-MM），还是仍在进行？",
        "background": "这项工作要解决什么问题，背景或目标是什么？",
        "action": "你具体采取了哪些行动？",
        "result": "你的行动产生了什么结果？",
        "metrics": "可以用比例、数量、时间、成本或规模确认哪些量化结果？",
    },
    "ja": {
        "identity": "この経験を端的に表すタイトルは何ですか？",
        "organization": "この経験はどの組織、チーム、顧客でのものですか？",
        "role": "この経験での具体的な役割または主な責任は何ですか？",
        "dates": "この経験の開始・終了時期（YYYY-MM）はいつですか。それとも現在も継続中ですか？",
        "background": "この仕事が対象とした課題、背景、目標は何ですか？",
        "action": "具体的に何をしましたか？",
        "result": "その行動によってどのような成果が生まれましたか？",
        "metrics": "割合、件数、時間、コスト、規模などで確認できる成果はありますか？",
    },
    "es": {
        "identity": "¿Qué título breve describe mejor esta experiencia?",
        "organization": "¿Con qué organización, equipo o cliente fue esta experiencia?",
        "role": "¿Cuál fue tu función específica o responsabilidad principal?",
        "dates": "¿Cuándo comenzó y terminó esta experiencia (AAAA-MM), o sigue vigente?",
        "background": "¿Qué problema, contexto u objetivo abordó este trabajo?",
        "action": "¿Qué hiciste concretamente?",
        "result": "¿Qué resultado produjo tu trabajo?",
        "metrics": "¿Qué resultado medible puedes confirmar, como porcentaje, cantidad, tiempo, coste o escala?",
    },
    "fr": {
        "identity": "Quel titre concis décrit le mieux cette expérience ?",
        "organization": "Auprès de quelle organisation, équipe ou clientèle cette expérience a-t-elle eu lieu ?",
        "role": "Quel était votre rôle précis ou votre responsabilité principale ?",
        "dates": "Quand cette expérience a-t-elle commencé et pris fin (AAAA-MM), ou est-elle toujours en cours ?",
        "background": "À quel problème, contexte ou objectif ce travail répondait-il ?",
        "action": "Qu'avez-vous fait concrètement ?",
        "result": "Quel résultat votre travail a-t-il produit ?",
        "metrics": "Quel résultat mesurable pouvez-vous confirmer : pourcentage, volume, durée, coût ou échelle ?",
    },
    "pt": {
        "identity": "Qual título curto descreve melhor esta experiência?",
        "organization": "Em qual organização, equipe ou cliente ocorreu esta experiência?",
        "role": "Qual foi sua função específica ou principal responsabilidade?",
        "dates": "Quando esta experiência começou e terminou (AAAA-MM), ou ainda está em andamento?",
        "background": "Qual problema, contexto ou objetivo este trabalho abordou?",
        "action": "O que você fez concretamente?",
        "result": "Qual resultado foi gerado pelo seu trabalho?",
        "metrics": "Qual resultado mensurável você pode confirmar, como percentual, quantidade, tempo, custo ou escala?",
    },
}


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
    *,
    language: str = "en",
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

    questions = _QUESTIONS.get(language, _QUESTIONS["en"])
    return CompletenessResult(
        completeness=max(0, min(100, completeness)),
        missing_dimensions=missing_dimensions,
        suggested_questions=[questions[dimension] for dimension in missing_dimensions],
    )


def question_for_dimension(dimension: str, language: str = "en") -> str:
    """Return deterministic localized guidance for one missing dimension."""
    questions = _QUESTIONS.get(language, _QUESTIONS["en"])
    return questions.get(
        dimension,
        {
            "zh": "还有哪些可核实的事实能让这段经历更清晰？",
            "ja": "この経験をより明確にする、確認可能な事実はほかにありますか？",
            "es": "¿Qué otro dato verificable haría más clara esta experiencia?",
            "fr": "Quel autre fait vérifiable rendrait cette expérience plus claire ?",
            "pt": "Que outro fato verificável tornaria esta experiência mais clara?",
        }.get(language, "What additional factual detail would make this experience clearer?"),
    )
