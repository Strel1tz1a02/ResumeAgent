"""AI Chat Run 只保留生命周期状态。"""

import sqlite3
from pathlib import Path

import pytest

from app.ai_chat.adapters import AdapterRegistry
from app.ai_chat.errors import ConversationEndedError
from app.ai_chat.graph import GraphRecovery
from app.ai_chat.protocol import (
    GraphOutcome,
    InteractionRequest,
    ResolveInteractionCommand,
)
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.services.ai_chat_service import AiChatService
from app.ai_chat.services.tool_service import ToolService
from app.ai_chat.tools.store import ToolCallStore
from app.ai_chat.streaming.events import RuntimeEvent
from app.ai_chat.tools.types import ToolContext
from app.jd_import.adapters import JDImportAdapter
from app.jd_import.agent.types import Assessment, CandidateJD
from app.jd_import.graph import JDImportGraphDependencies
from app.scripts.migrate_remove_ai_chat_run_result import migrate
from sqlalchemy import create_engine


def test_result_column_is_removed_idempotently(tmp_path: Path) -> None:
    path = tmp_path / "runs.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE ai_chat_runs ("
            "id INTEGER PRIMARY KEY, status VARCHAR NOT NULL, result JSON NOT NULL)"
        )
        connection.execute(
            "INSERT INTO ai_chat_runs (id, status, result) VALUES (1, 'suspended', '{}')"
        )

    engine = create_engine(f"sqlite:///{path}")
    try:
        migrate(engine)
        migrate(engine)
        with engine.connect() as connection:
            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(ai_chat_runs)"
                ).all()
            }
            row = connection.exec_driver_sql(
                "SELECT id, status FROM ai_chat_runs"
            ).one()
    finally:
        engine.dispose()

    assert "result" not in columns
    assert row == (1, "suspended")


class _QuestionRunner:
    def __init__(self, run_id: int, tool_call_id: int, batch_id: str) -> None:
        self.run_id = run_id
        self.tool_call_id = tool_call_id
        self.batch_id = batch_id

    async def recover(self, **kwargs):  # type: ignore[no-untyped-def]
        return GraphRecovery(
            outcome=GraphOutcome.waiting(
                InteractionRequest(
                    interaction_id=self.tool_call_id,
                    kind="question_batch",
                    payload={"batch_id": self.batch_id},
                )
            )
        )

    async def resume(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["command"].run_id == self.run_id
        assert kwargs["command"].interaction_id == self.tool_call_id
        yield RuntimeEvent(
            "result.available",
            {
                "kind": "jd_import",
                "result": {"persisted_ids": [1], "errors": []},
            },
        )
        yield GraphOutcome.completed()


async def test_question_resolution_uses_tool_call_and_replays(isolated_db) -> None:  # type: ignore[no-untyped-def]
    repositories = RepositoryFactory()
    dependencies = JDImportGraphDependencies(None, None, None)  # type: ignore[arg-type]
    adapter = JDImportAdapter(dependencies)
    registry = AdapterRegistry()
    registry.register(adapter)
    tools = ToolService(ToolCallStore(isolated_db.session, repositories)).bind_tools(
        adapter.get_tools(), adapter.get_tool_approval_policy()
    )

    async with isolated_db.session() as session:
        repos = repositories.create(session)
        conversation = await repos.conversations.create(
            adapter="JDImportAdapter",
            subject={"type": "jd_import", "id": "new"},
            scope={},
            language="zh",
        )
        run = await repos.runs.create(
            conversation_id=conversation.id,
            kind="user_turn",
            tools_enabled=True,
        )
        await repos.runs.transition(
            run.id, from_statuses={"running"}, to_status="suspended"
        )
        await session.commit()

    context = ToolContext(
        conversation_id=conversation.id,
        run_id=run.id,
        subject={"type": "jd_import", "id": "new"},
        scope={},
        adapter_context={
            "assessment": Assessment(
                candidates=[CandidateJD(jd_key="jd-1", missing_fields=["company"])],
                conflicts=[],
            ).model_dump(mode="json"),
            "asked_question_keys": [],
            "round": 0,
        },
    )
    call = await tools.validate_system_call(
        context,
        identity="jd-import:questions:1",
        name="ask_jd_questions",
        arguments={
            "questions": [
                {
                    "question_key": "missing:jd-1:company",
                    "prompt": "公司名称是什么？",
                    "mode": "text",
                    "options": [],
                }
            ]
        },
    )
    call = await tools.request_input(call["tool_call_id"])
    batch = call["interaction_payload"]
    runner = _QuestionRunner(run.id, call["tool_call_id"], batch["batch_id"])
    service = AiChatService(registry, runner, repositories)  # type: ignore[arg-type]
    answer = {
        "batch_id": batch["batch_id"],
        "client_resolution_id": "answer-1",
        "answers": [
            {
                "question_id": batch["questions"][0]["question_id"],
                "value": "Acme",
                "skipped": False,
            }
        ],
    }
    events = [
        event
        async for event in service.resolve_interaction(
            ResolveInteractionCommand(
                run_id=run.id,
                interaction_id=call["tool_call_id"],
                kind="question_batch",
                client_resolution_id="answer-1",
                payload={
                    key: value
                    for key, value in answer.items()
                    if key != "client_resolution_id"
                },
            )
        )
    ]
    replay = [
        event
        async for event in service.resolve_interaction(
            ResolveInteractionCommand(
                run_id=run.id,
                interaction_id=call["tool_call_id"],
                kind="question_batch",
                client_resolution_id="answer-1",
                payload={
                    key: value
                    for key, value in answer.items()
                    if key != "client_resolution_id"
                },
            )
        )
    ]

    assert [event.type for event in events] == [
        "interaction.resolved",
        "result.available",
        "run.completed",
    ]
    assert [event.type for event in replay] == ["command.replayed"]
    resolved = await tools.get_call(call["tool_call_id"])
    assert resolved["status"] == "resolved"

    await service.close_conversation(conversation.id, "user_closed")
    with pytest.raises(ConversationEndedError):
        _ = [
            event
            async for event in service.resolve_interaction(
                ResolveInteractionCommand(
                    run_id=run.id,
                    interaction_id=call["tool_call_id"],
                    kind="question_batch",
                    client_resolution_id="answer-1",
                    payload={
                        key: value
                        for key, value in answer.items()
                        if key != "client_resolution_id"
                    },
                )
            )
        ]


async def test_close_conversation_settles_run_and_message_through_lifecycle(
    isolated_db,
) -> None:  # type: ignore[no-untyped-def]
    repositories = RepositoryFactory()
    async with isolated_db.session() as session:
        repos = repositories.create(session)
        conversation = await repos.conversations.create(
            adapter="unused",
            subject={"type": "test", "id": "1"},
            scope={},
            language="zh",
        )
        run = await repos.runs.create(
            conversation_id=conversation.id,
            kind="user_turn",
            tools_enabled=True,
        )
        message = await repos.messages.create(
            conversation_id=conversation.id,
            run_id=run.id,
            role="assistant",
            content="partial",
            status="generating",
        )
        await session.commit()

    service = AiChatService(AdapterRegistry(), object(), repositories)  # type: ignore[arg-type]
    await service.close_conversation(conversation.id, "user_closed")

    async with isolated_db.session() as session:
        repos = repositories.create(session)
        stored_conversation = await repos.conversations.get(conversation.id)
        stored_run = await repos.runs.get(run.id)
        stored_message = await session.get(type(message), message.id)
        assert stored_conversation is not None
        assert stored_conversation.status == "ended"
        assert stored_run is not None and stored_run.status == "cancelled"
        assert stored_message is not None and stored_message.status == "cancelled"
