"""覆盖 JD 解析、提取、补问与持久化的单一 Graph。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.streaming.model import ToolCallsCompleted
from app.ai_chat.tools.types import ToolContext
from app.jd_import.agent.evidence import assess_candidates
from app.jd_import.agent.input_parser import parse_mixed_input
from app.jd_import.agent.model import (
    ExtractionRequest,
    JDImportModel,
    UrlCandidate,
    UrlSelectionRequest,
)
from app.jd_import.agent.questions import validate_batch_answer
from app.jd_import.agent.state import JDImportState
from app.jd_import.agent.types import (
    Assessment,
    AssessmentError,
    CandidateJD,
    Conflict,
    ImportErrorItem,
    ImportResult,
    ImportSource,
    QuestionBatch,
    QuestionBatchAnswer,
)
from app.jd_import.sources import PageSourceProvider, UrlPolicy


@dataclass(frozen=True)
class JDImportGraphDependencies:
    model: JDImportModel
    page_sources: PageSourceProvider
    url_policy: UrlPolicy


def _emit(event: str, data: dict[str, Any]) -> None:
    get_stream_writer()({"event": event, "data": data})


def _tool_context(state: JDImportState, adapter_context: dict[str, Any]) -> ToolContext:
    return ToolContext(
        conversation_id=state["conversation_id"],
        run_id=state["run_id"],
        subject={"type": "jd_import", "id": "new"},
        scope={},
        adapter_context=adapter_context,
    )


def _assessment(state: JDImportState) -> Assessment:
    return Assessment(
        candidates=[CandidateJD.model_validate(item) for item in state["candidates"]],
        conflicts=[Conflict.model_validate(item) for item in state["conflicts"]],
        errors=[AssessmentError.model_validate(item) for item in state["assessment_errors"]],
    )


def _has_unasked_questions(state: JDImportState) -> bool:
    asked = set(state["questions"]["asked_question_keys"])
    keys = {item.conflict_key for item in _assessment(state).conflicts}
    keys.update(
        f"missing:{candidate.jd_key}:{field}"
        for candidate in _assessment(state).candidates
        for field in candidate.missing_fields
    )
    return bool(keys - asked)


def build_jd_import_graph(
    runtime: AiChatRuntime,
    deps: JDImportGraphDependencies,
) -> StateGraph:
    question_handler = runtime.tools.model_handlers.get("ask_jd_questions")
    if question_handler is None:
        raise ToolProtocolError("JD import runtime has no question Tool")
    planning_runtime = runtime.bind_tools({question_handler.name: question_handler})

    async def parse_input(state: JDImportState) -> dict[str, Any]:
        parsed = parse_mixed_input(state["input"]["raw_input"])
        return {
            "input": {"raw_input": parsed.raw_input, "detected_urls": parsed.urls},
            "sources": [item.model_dump(mode="json") for item in parsed.sources],
        }

    async def resolve_urls(state: JDImportState) -> dict[str, Any]:
        sources = [ImportSource.model_validate(item) for item in state["sources"]]
        url_sources = [item for item in sources if item.type == "url"]
        if not url_sources:
            return {}
        selection = await deps.model.select_urls(
            UrlSelectionRequest(
                urls=[
                    UrlCandidate(source_id=item.source_id, url=item.source_url or "")
                    for item in url_sources
                ],
                existing_text="\n".join(
                    item.content for item in sources if item.type == "text"
                ),
            )
        )
        selected = set(selection.selected_source_ids)
        for source in sources:
            if source.type != "url" or source.source_id not in selected:
                if source.type == "url":
                    source.url_status = "skipped"
                continue
            try:
                fetched = await deps.page_sources.fetch(
                    deps.url_policy.validate(source.source_url or "")
                )
                source.url_status = fetched.status
                source.content = fetched.text
                source.source_url = fetched.final_url or source.source_url
            except ValueError:
                source.url_status = "blocked"
        return {"sources": [item.model_dump(mode="json") for item in sources]}

    async def extract(state: JDImportState) -> dict[str, Any]:
        result = await deps.model.extract(
            ExtractionRequest(
                sources=[ImportSource.model_validate(item) for item in state["sources"]],
                prior_candidates=[
                    CandidateJD.model_validate(item) for item in state["candidates"]
                ],
            )
        )
        return {
            "candidates": [item.model_dump(mode="json") for item in result.candidates],
            "conflicts": [item.model_dump(mode="json") for item in result.conflicts],
        }

    async def assess(state: JDImportState) -> dict[str, Any]:
        assessment = assess_candidates(
            [ImportSource.model_validate(item) for item in state["sources"]],
            [CandidateJD.model_validate(item) for item in state["candidates"]],
            [Conflict.model_validate(item) for item in state["conflicts"]],
        )
        return {
            "candidates": [item.model_dump(mode="json") for item in assessment.candidates],
            "conflicts": [item.model_dump(mode="json") for item in assessment.conflicts],
            "assessment_errors": [
                item.model_dump(mode="json") for item in assessment.errors
            ],
        }

    def route_assessment(state: JDImportState) -> Literal["plan_questions", "persist"]:
        if state["questions"]["round"] >= 3 or not _has_unasked_questions(state):
            return "persist"
        return "plan_questions"

    async def plan_questions(state: JDImportState) -> dict[str, Any]:
        snapshot = {
            "assessment": _assessment(state).model_dump(mode="json"),
            "sources": state["sources"],
            "history": state["questions"]["answers"],
            "round": state["questions"]["round"],
            "asked_question_keys": state["questions"]["asked_question_keys"],
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "Review the complete JD state. Call ask_jd_questions once if "
                    "clarification is useful; otherwise return without a Tool Call."
                ),
            },
            {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False)},
        ]
        calls: tuple[str, ...] = ()
        async for event in planning_runtime.stream_model(
            messages=messages, tools_enabled=True, max_tokens=4096
        ):
            if isinstance(event, ToolCallsCompleted):
                calls = event.calls
        if not calls:
            return {"question_tool_call_id": None}
        if len(calls) != 1:
            raise ToolProtocolError("Question planning accepts at most one Tool Call")
        round_value = state["questions"]["round"] + 1
        call = await planning_runtime.tools.validate_model_call_as(
            _tool_context(
                state,
                {
                    "assessment": snapshot["assessment"],
                    "asked_question_keys": snapshot["asked_question_keys"],
                    "round": state["questions"]["round"],
                },
            ),
            calls[0],
            identity=f"jd-import:questions:{round_value}",
            expected_name="ask_jd_questions",
        )
        waiting = await planning_runtime.tools.request_input(call["tool_call_id"])
        batch = QuestionBatch.model_validate(waiting["proposal_payload"])
        questions = dict(state["questions"])
        questions["round"] = batch.round
        questions["asked_question_keys"] = [
            *questions["asked_question_keys"],
            *(item.question_key for item in batch.questions),
        ]
        return {"questions": questions, "question_tool_call_id": call["tool_call_id"]}

    def route_planning(state: JDImportState) -> Literal["ask_questions", "persist"]:
        return "ask_questions" if state.get("question_tool_call_id") else "persist"

    async def ask_questions(state: JDImportState) -> dict[str, Any]:
        tool_call_id = state.get("question_tool_call_id")
        if not isinstance(tool_call_id, int):
            raise ToolProtocolError("Question node has no Tool Call identity")
        call = await runtime.tools.get_call(tool_call_id)
        if call["status"] == "resolved":
            return {}
        if call["status"] != "awaiting_input":
            raise ToolProtocolError("Question Tool Call is not awaiting input")
        batch = QuestionBatch.model_validate(call["proposal_payload"])
        _emit("jd.questions.requested", batch.model_dump(mode="json"))
        resumed = interrupt({"type": "question_batch", "batch_id": batch.batch_id})
        if not isinstance(resumed, dict) or resumed.get("tool_call_id") != tool_call_id:
            raise ToolProtocolError("Question resume identity does not match")
        return {}

    async def merge_answers(state: JDImportState) -> dict[str, Any]:
        tool_call_id = state.get("question_tool_call_id")
        if not isinstance(tool_call_id, int):
            raise ToolProtocolError("Answer merge has no Tool Call identity")
        call = await runtime.tools.get_call(tool_call_id)
        if call["status"] != "resolved" or call["result"] is None:
            raise ToolProtocolError("Question Tool Call has no durable answer")
        batch = QuestionBatch.model_validate(call["proposal_payload"])
        answer = QuestionBatchAnswer.model_validate(call["result"])
        additions = validate_batch_answer(batch, answer)
        await runtime.tools.consume_result(tool_call_id)
        questions = dict(state["questions"])
        questions["answers"] = [*questions["answers"], answer.model_dump(mode="json")]
        return {
            "sources": [
                *state["sources"],
                *(item.model_dump(mode="json") for item in additions),
            ],
            "questions": questions,
            "question_tool_call_id": None,
        }

    async def persist(state: JDImportState) -> dict[str, Any]:
        candidates = [CandidateJD.model_validate(item) for item in state["candidates"]]
        by_key = {item.jd_key: item for item in candidates}
        for conflict_data in state["conflicts"]:
            conflict = Conflict.model_validate(conflict_data)
            if conflict.required:
                for jd_key in conflict.target_jd_keys or list(by_key):
                    candidate = by_key.get(jd_key)
                    if candidate is not None and conflict.field not in candidate.missing_fields:
                        candidate.missing_fields.append(conflict.field)

        ids: list[int] = []
        errors: list[ImportErrorItem] = []
        for candidate in candidates:
            try:
                context = _tool_context(state, {})
                call = await runtime.tools.validate_system_call(
                    context,
                    identity=f"jd-import:persist:{candidate.jd_key}",
                    name="persist_jd",
                    arguments={"candidate": candidate.model_dump(mode="json")},
                )
                result = await runtime.tools.execute_call(context, call["tool_call_id"])
                information_id = result.payload.get("information_id")
                if not isinstance(information_id, int):
                    raise ToolProtocolError("persist_jd returned no information_id")
                ids.append(information_id)
            except Exception:  # noqa: BLE001 - isolate each candidate and hide internals
                errors.append(
                    ImportErrorItem(code="persistence_failed", jd_key=candidate.jd_key)
                )
        result = ImportResult(persisted_ids=ids, errors=errors)
        payload = result.model_dump(mode="json")
        _emit("jd.import.completed", payload)
        return {"result": payload}

    graph = StateGraph(JDImportState)
    for name, node in (
        ("parse_input", parse_input),
        ("resolve_urls", resolve_urls),
        ("extract", extract),
        ("assess", assess),
        ("plan_questions", plan_questions),
        ("ask_questions", ask_questions),
        ("merge_answers", merge_answers),
        ("persist", persist),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "parse_input")
    graph.add_edge("parse_input", "resolve_urls")
    graph.add_edge("resolve_urls", "extract")
    graph.add_edge("extract", "assess")
    graph.add_conditional_edges("assess", route_assessment)
    graph.add_conditional_edges("plan_questions", route_planning)
    graph.add_edge("ask_questions", "merge_answers")
    graph.add_edge("merge_answers", "extract")
    graph.add_edge("persist", END)
    return graph
