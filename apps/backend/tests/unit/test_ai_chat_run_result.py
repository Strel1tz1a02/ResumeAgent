"""AI Chat Run 只保留生命周期状态。"""

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from app.ai_chat.adapters import AdapterRegistry
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.services.ai_chat_service import AiChatService
from app.ai_chat.services.tool_call_service import ToolCallService
from app.ai_chat.streaming.events import AiChatEvent
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
        self.values = {
            "run_id": run_id,
            "question_tool_call_id": tool_call_id,
            "questions": {"answers": []},
        }
        self.batch_id = batch_id

    async def get_state(self, **kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(values=self.values)

    async def resume_value(self, **kwargs):  # type: ignore[no-untyped-def]
        self.values["question_tool_call_id"] = None
        self.values["questions"]["answers"].append({"batch_id": self.batch_id})
        yield AiChatEvent("jd.import.completed", {"persisted_ids": [1], "errors": []})


async def test_question_resolution_uses_tool_call_and_replays(isolated_db) -> None:  # type: ignore[no-untyped-def]
    repositories = RepositoryFactory()
    dependencies = JDImportGraphDependencies(None, None, None)  # type: ignore[arg-type]
    adapter = JDImportAdapter(dependencies)
    registry = AdapterRegistry()
    registry.register(adapter)
    tools = ToolCallService(
        isolated_db.session, repositories
    ).bind_handlers(adapter.get_tool_handlers())

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
    batch = call["proposal_payload"]
    runner = _QuestionRunner(run.id, call["tool_call_id"], batch["batch_id"])
    service = AiChatService(registry, runner, repositories)  # type: ignore[arg-type]
    answer = {
        "type": "question_batch_answer",
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
    await tools.resolve_input(
        call["tool_call_id"], "answer-1", answer
    )

    events = [
        event
        async for event in service.resolve_question_batch(
            conversation.id, batch["batch_id"], answer
        )
    ]
    replay = [
        event
        async for event in service.resolve_question_batch(
            conversation.id, batch["batch_id"], answer
        )
    ]

    assert [event.event for event in events] == ["jd.import.completed"]
    assert [event.event for event in replay] == ["message.replayed"]
    resolved = await tools.get_call(call["tool_call_id"])
    assert resolved["status"] == "resolved"
