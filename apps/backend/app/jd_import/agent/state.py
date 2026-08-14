"""可序列化为 JSON 的 JD 导入 Graph 检查点状态。"""

from __future__ import annotations

from typing import Any, TypedDict

from app.jd_import.agent.types import ParsedInput


class ImportInputState(TypedDict):
    raw_input: str
    detected_urls: list[str]


class QuestionState(TypedDict):
    round: int
    asked_question_keys: list[str]
    answers: list[dict[str, Any]]


class ImportResultState(TypedDict):
    persisted_ids: list[int]
    errors: list[dict[str, Any]]


class JDImportState(TypedDict):
    conversation_id: int
    run_id: int
    input: ImportInputState
    sources: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    assessment_errors: list[dict[str, Any]]
    questions: QuestionState
    result: ImportResultState
    question_tool_call_id: int | None


def initial_state(
    *,
    conversation_id: int,
    run_id: int,
    raw_input: str,
    parsed: ParsedInput,
) -> JDImportState:
    """构建完整初始检查点，不在状态中保留 Pydantic 对象。"""
    return JDImportState(
        conversation_id=conversation_id,
        run_id=run_id,
        input={"raw_input": raw_input, "detected_urls": list(parsed.urls)},
        sources=[item.model_dump(mode="json") for item in parsed.sources],
        candidates=[],
        conflicts=[],
        assessment_errors=[],
        questions={
            "round": 0,
            "asked_question_keys": [],
            "answers": [],
        },
        result={"persisted_ids": [], "errors": []},
        question_tool_call_id=None,
    )
