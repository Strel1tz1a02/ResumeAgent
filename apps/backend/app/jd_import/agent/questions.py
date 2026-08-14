"""确定性生成合并问题批次，并校验整批答案。"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping

from app.jd_import.agent.types import (
    Assessment,
    Conflict,
    ImportSource,
    Question,
    QuestionBatch,
    QuestionBatchAnswer,
    QuestionDraft,
)

_REQUIRED_FIELDS = {"company", "job_name", "requirements"}
_FIELD_LABELS = {
    "company": "公司名称",
    "job_name": "岗位名称",
    "requirements": "岗位要求",
    "type": "岗位类型",
    "location": "工作地点",
    "source_url": "岗位链接",
}


class QuestionAnswerError(ValueError):
    """问题批次回答的稳定校验错误。"""


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _question_from_conflict(conflict: Conflict) -> tuple[int, Question]:
    if conflict.kind == "ownership":
        priority = 0
        prompt = "这段信息属于哪个 JD？"
    elif conflict.kind == "conflict" and conflict.required:
        priority = 1
        prompt = f"请确认 {_FIELD_LABELS.get(conflict.field, conflict.field)} 的正确值。"
    elif conflict.kind == "source_access":
        priority = 3
        prompt = "该链接无法访问，请粘贴页面内容或选择跳过。"
    else:
        priority = 4
        prompt = f"请确认 {_FIELD_LABELS.get(conflict.field, conflict.field)}。"
    mode = "choice" if len(conflict.values) >= 2 else "text"
    return priority, Question(
        question_id="",
        question_key=conflict.conflict_key,
        kind=conflict.kind,
        target_jd_keys=conflict.target_jd_keys,
        field=conflict.field,
        prompt=prompt,
        mode=mode,
        options=conflict.values if mode == "choice" else [],
        allow_custom=True,
    )


def _missing_questions(assessment: Assessment) -> list[tuple[int, Question]]:
    result: list[tuple[int, Question]] = []
    for candidate in assessment.candidates:
        for field in candidate.missing_fields:
            required = field in _REQUIRED_FIELDS
            result.append(
                (
                    2 if required else 4,
                    Question(
                        question_id="",
                        question_key=f"missing:{candidate.jd_key}:{field}",
                        kind="missing",
                        target_jd_keys=[candidate.jd_key],
                        field=field,
                        prompt=(
                            f"请补充 JD {candidate.jd_key} 的 "
                            f"{_FIELD_LABELS.get(field, field)}。"
                        ),
                        mode="text",
                        allow_custom=True,
                    ),
                )
            )
    return result


def build_question_batch(
    assessment: Assessment,
    *,
    asked_keys: Collection[str],
    round_number: int,
    run_id: int,
    model_questions: Mapping[str, QuestionDraft] | None = None,
) -> QuestionBatch | None:
    """按固定业务优先级生成最多十二个新问题。"""
    if round_number >= 3:
        return None
    enriched = model_questions or {}
    ranked = [_question_from_conflict(item) for item in assessment.conflicts]
    ranked.extend(_missing_questions(assessment))
    ranked.sort(key=lambda item: item[0])

    selected: list[Question] = []
    seen = set(asked_keys)
    for _, question in ranked:
        if question.question_key in seen:
            continue
        seen.add(question.question_key)
        draft = enriched.get(question.question_key)
        if draft is not None:
            question.prompt = draft.prompt
            question.mode = draft.mode
            question.options = list(draft.options) if draft.mode == "choice" else []
        selected.append(question)
        if len(selected) == 12:
            break
    if not selected:
        return None

    round_value = round_number + 1
    batch_seed = f"{run_id}:{round_value}:" + "|".join(
        item.question_key for item in selected
    )
    batch_id = f"batch-{_digest(batch_seed)}"
    for question in selected:
        question.question_id = f"question-{_digest(f'{batch_id}:{question.question_key}')}"
    return QuestionBatch(batch_id=batch_id, round=round_value, questions=selected)


def build_requested_question_batch(
    assessment: Assessment,
    drafts: list[QuestionDraft],
    *,
    asked_keys: Collection[str],
    round_number: int,
    run_id: int,
) -> QuestionBatch:
    """严格生成规划模型选中的有效问题子集。"""
    if round_number >= 3:
        raise QuestionAnswerError("question_round_limit")
    if not drafts or len(drafts) > 12:
        raise QuestionAnswerError("invalid_question_count")
    available = {
        question.question_key: question
        for _priority, question in [
            *(_question_from_conflict(item) for item in assessment.conflicts),
            *_missing_questions(assessment),
        ]
    }
    seen = set(asked_keys)
    selected: list[Question] = []
    for draft in drafts:
        if draft.question_key in seen:
            raise QuestionAnswerError("duplicate_question_key")
        base = available.get(draft.question_key)
        if base is None:
            raise QuestionAnswerError("unknown_question_key")
        seen.add(draft.question_key)
        base.prompt = draft.prompt
        base.mode = draft.mode
        base.options = list(draft.options) if draft.mode == "choice" else []
        selected.append(base)

    round_value = round_number + 1
    batch_seed = f"{run_id}:{round_value}:" + "|".join(
        item.question_key for item in selected
    )
    batch_id = f"batch-{_digest(batch_seed)}"
    for question in selected:
        question.question_id = f"question-{_digest(f'{batch_id}:{question.question_key}')}"
    return QuestionBatch(batch_id=batch_id, round=round_value, questions=selected)


def validate_batch_answer(
    batch: QuestionBatch,
    answer: QuestionBatchAnswer,
) -> list[ImportSource]:
    """要求每题都有回答，并将未跳过的答案转换为新来源。"""
    if answer.batch_id != batch.batch_id:
        raise QuestionAnswerError("batch_mismatch")
    expected = {item.question_id: item for item in batch.questions}
    answer_ids = [item.question_id for item in answer.answers]
    if len(answer_ids) != len(set(answer_ids)):
        raise QuestionAnswerError("duplicate_question")
    if set(answer_ids) != set(expected):
        raise QuestionAnswerError("incomplete_batch")

    sources: list[ImportSource] = []
    for item in answer.answers:
        if item.question_id not in expected:
            raise QuestionAnswerError("unknown_question")
        if item.skipped:
            continue
        question = expected[item.question_id]
        value = item.value or ""
        sources.append(
            ImportSource(
                source_id=f"source:answer:{batch.batch_id}:{item.question_id}",
                type="user_answer",
                content=(
                    f"Question: {question.prompt}\n"
                    f"Targets: {', '.join(question.target_jd_keys) or 'unassigned'}\n"
                    f"Field: {question.field}\nAnswer: {value}"
                ),
            )
        )
    return sources
