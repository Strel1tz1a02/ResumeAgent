"""统一 Agent Runtime 协议的边界测试。"""

import logging

import pytest

from app.ai_chat.errors import InteractionStateError
from app.ai_chat.protocol import (
    GraphOutcome,
    GraphResumeCommand,
    InteractionRequest,
    ResolveInteractionCommand,
)
from app.ai_chat.streaming.events import RuntimeEvent, tool_result_event
from app.ai_chat.streaming.sse import stream_runtime_events


def test_runtime_event_exposes_one_frontend_envelope() -> None:
    event = RuntimeEvent("output.delta", {"text": "你好"}).bind(run_id=7, sequence=3)

    assert event.envelope() == {
        "type": "output.delta",
        "run_id": 7,
        "sequence": 3,
        "payload": {"text": "你好"},
    }
    assert not hasattr(event, "event")
    assert not hasattr(event, "data")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"type": "", "payload": {}},
        {"type": "run.completed\nevent: forged", "payload": {}},
        {"type": "output.delta", "payload": []},
        {"type": "run.completed", "payload": {}, "run_id": 0},
        {"type": "run.completed", "payload": {}, "run_id": True},
        {"type": "run.completed", "payload": {}, "run_id": " "},
        {"type": "run.completed", "payload": {}, "sequence": 0},
    ],
)
def test_runtime_event_rejects_malformed_envelopes(kwargs) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises((TypeError, ValueError)):
        RuntimeEvent(**kwargs)


def test_tool_result_uses_generic_result_event() -> None:
    event = tool_result_event(
        tool_name="content_change",
        tool_call_id=12,
        result={"outcome": "applied", "experience": {"id": 1}},
    )

    assert event.type == "result.available"
    assert event.payload == {
        "kind": "tool_result",
        "tool_name": "content_change",
        "tool_call_id": 12,
        "outcome": "applied",
        "result": {"outcome": "applied", "experience": {"id": 1}},
    }


def test_interaction_resolution_keeps_client_payload_out_of_checkpoint() -> None:
    command = ResolveInteractionCommand(
        run_id=5,
        interaction_id=9,
        kind="question_batch",
        client_resolution_id="answer-1",
        payload={"secret_answer": "Acme"},
    )
    resume = GraphResumeCommand(run_id=5, interaction_id=command.interaction_id)

    assert resume.resume_value() == {
        "type": "interaction_resolved",
        "run_id": 5,
        "interaction_id": 9,
    }
    assert "secret_answer" not in str(resume.resume_value())


def test_graph_outcome_enforces_interaction_invariant() -> None:
    request = InteractionRequest(
        interaction_id=4,
        kind="approval",
        payload={"proposal": {"field": "background"}},
    )

    assert GraphOutcome.waiting(request).interaction == request
    assert GraphOutcome.completed().interaction is None
    with pytest.raises(ValueError):
        GraphOutcome(status="waiting")
    with pytest.raises(ValueError):
        GraphOutcome(status="completed", interaction=request)
    with pytest.raises(ValueError):
        GraphOutcome(status="unknown")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: InteractionRequest(  # type: ignore[arg-type]
            interaction_id="4", kind="approval", payload={}
        ),
        lambda: InteractionRequest(interaction_id=4, kind=" ", payload={}),
        lambda: ResolveInteractionCommand(  # type: ignore[arg-type]
            run_id="5",
            interaction_id=9,
            kind="approval",
            client_resolution_id="resolve-1",
            payload={},
        ),
        lambda: ResolveInteractionCommand(
            run_id=5,
            interaction_id=9,
            kind="approval",
            client_resolution_id=" ",
            payload={},
        ),
        lambda: GraphResumeCommand(run_id=5, interaction_id="9"),  # type: ignore[arg-type]
    ],
)
def test_runtime_protocol_rejects_values_that_only_match_type_hints(
    factory,  # type: ignore[no-untyped-def]
) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()


async def test_sse_maps_stable_runtime_errors_without_domain_overrides() -> None:
    async def events():  # type: ignore[no-untyped-def]
        yield RuntimeEvent("run.started", {}, run_id=7)
        raise InteractionStateError("invalid boundary")

    records = [
        record
        async for record in stream_runtime_events(
            events(),
            logger=logging.getLogger("test-runtime-sse"),
        )
    ]

    assert '"sequence": 1' in records[1]
    assert '"type": "run.failed"' in records[2]
    assert '"run_id": 7' in records[2]
    assert '"sequence": 2' in records[2]
    assert '"code": "interaction_state_error"' in records[2]
