"""问题规划与整批答案校验测试。"""

import pytest
from app.jd_import.agent.questions import (
    QuestionAnswerError,
    build_question_batch,
    validate_batch_answer,
)
from app.jd_import.agent.types import (
    Assessment,
    CandidateJD,
    Conflict,
    QuestionAnswer,
    QuestionBatchAnswer,
)


def _assessment() -> Assessment:
    conflicts = [
        Conflict(
            conflict_key="ownership:fragment",
            kind="ownership",
            target_jd_keys=["jd-1", "jd-2"],
            field="requirement",
            values=["jd-1", "jd-2"],
            required=True,
        ),
        Conflict(
            conflict_key="conflict:jd-1:company",
            kind="conflict",
            target_jd_keys=["jd-1"],
            field="company",
            values=["Acme", "Example"],
            required=True,
        ),
        Conflict(
            conflict_key="source_access:url-1",
            kind="source_access",
            target_jd_keys=[],
            field="source_url",
            values=["https://example.com/job"],
        ),
    ]
    candidates = [
        CandidateJD(
            jd_key="jd-1",
            missing_fields=["company", "job_name", "requirements", "type", "location"],
        ),
        CandidateJD(
            jd_key="jd-2",
            missing_fields=["company", "job_name", "requirements", "type", "location"],
        ),
    ]
    return Assessment(candidates=candidates, conflicts=conflicts)


def test_question_batch_orders_dedupes_and_limits_to_twelve() -> None:
    batch = build_question_batch(
        _assessment(),
        asked_keys={"missing:jd-2:location"},
        round_number=0,
        run_id=7,
    )

    assert batch is not None
    assert batch.round == 1
    assert len(batch.questions) == 12
    assert [question.kind for question in batch.questions[:4]] == [
        "ownership",
        "conflict",
        "missing",
        "missing",
    ]
    assert batch.questions[0].mode == "choice"
    assert batch.questions[0].allow_custom is True
    assert all(question.question_key != "missing:jd-2:location" for question in batch.questions)


def test_round_three_produces_no_more_questions() -> None:
    assert (
        build_question_batch(
            _assessment(), asked_keys=set(), round_number=3, run_id=7
        )
        is None
    )


def test_validate_batch_answer_requires_exact_question_set() -> None:
    batch = build_question_batch(
        _assessment(), asked_keys=set(), round_number=0, run_id=7
    )
    assert batch is not None
    first = batch.questions[0]

    with pytest.raises(QuestionAnswerError, match="batch_mismatch"):
        validate_batch_answer(
            batch,
            QuestionBatchAnswer(
                batch_id="wrong",
                answers=[],
            ),
        )
    with pytest.raises(QuestionAnswerError, match="incomplete_batch"):
        validate_batch_answer(
            batch,
            QuestionBatchAnswer(
                batch_id=batch.batch_id,
                answers=[QuestionAnswer(question_id=first.question_id, value="jd-1")],
            ),
        )


def test_validate_batch_answer_creates_sources_and_ignores_skips() -> None:
    assessment = Assessment(
        candidates=[CandidateJD(jd_key="jd-1", missing_fields=["company", "type"])],
        conflicts=[],
    )
    batch = build_question_batch(
        assessment, asked_keys=set(), round_number=0, run_id=9
    )
    assert batch is not None
    answers = QuestionBatchAnswer(
        batch_id=batch.batch_id,
        answers=[
            QuestionAnswer(question_id=batch.questions[0].question_id, value="Acme"),
            QuestionAnswer(question_id=batch.questions[1].question_id, skipped=True),
        ],
    )

    sources = validate_batch_answer(batch, answers)

    assert len(sources) == 1
    assert sources[0].type == "user_answer"
    assert "Acme" in sources[0].content
