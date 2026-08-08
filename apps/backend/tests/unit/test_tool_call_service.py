"""Tests for durable AI Chat Tool Call persistence."""

import sqlite3

import pytest
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from app.ai_chat.models import AiChatToolCall
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.tools.buffer import AssembledToolCall
from app.ai_chat.tools.handler import ToolContext

try:
    from app.ai_chat.tools.lifecycle import ApprovalRequired, ToolLifecycle
except ImportError:  # The active graph refactor intentionally removes it.
    ApprovalRequired = None  # type: ignore[misc,assignment]
    ToolLifecycle = None  # type: ignore[misc,assignment]
from app.database import Database
from app.scripts.migrate_ai_chat_tool_call_state import (
    migrate as migrate_ai_chat_tool_call_state,
)


class _ReplayArguments(BaseModel):
    value: str


class _NeverInvokeHandler:
    name = "demo"
    arguments_schema = _ReplayArguments

    async def invoke(self, context, arguments):  # type: ignore[no-untyped-def]
        raise AssertionError("validated replay must not regenerate trusted payloads")


def test_tool_call_state_migration_backfills_validated(tmp_path) -> None:
    path = tmp_path / "legacy-tool-state.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "CREATE TABLE ai_chat_tool_calls ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL, "
            "run_id INTEGER NOT NULL, tool_call_index INTEGER NOT NULL, "
            "provider_tool_call_id VARCHAR(200), tool_name VARCHAR(160) NOT NULL, "
            "arguments JSON NOT NULL, proposal_payload JSON, guard_payload JSON, "
            "status VARCHAR(24) NOT NULL, decision VARCHAR(16), tool_result JSON, "
            "delivery_status VARCHAR(16), client_resolution_id VARCHAR(160), "
            "resolved_at VARCHAR, created_at VARCHAR NOT NULL, updated_at VARCHAR NOT NULL);"
        )
        rows = [
            (1, "received", '{"proposal":1}', '{"guard":1}', None),
            (2, "received", None, None, None),
            (3, "awaiting_approval", '{"proposal":3}', '{"guard":3}', None),
            (4, "resolved", None, None, '{"outcome":"done"}'),
        ]
        connection.executemany(
            "INSERT INTO ai_chat_tool_calls "
            "(id,conversation_id,run_id,tool_call_index,tool_name,arguments,"
            "proposal_payload,guard_payload,status,tool_result,created_at,updated_at) "
            "VALUES (?,1,1,?, 'demo', '{}', ?, ?, ?, ?, 'now', 'now')",
            [
                (row_id, row_id - 1, proposal, guard, status, result)
                for row_id, status, proposal, guard, result in rows
            ],
        )
    engine = create_engine(f"sqlite:///{path}")
    try:
        migrate_ai_chat_tool_call_state(engine)
        migrate_ai_chat_tool_call_state(engine)
        with engine.connect() as connection:
            values = connection.exec_driver_sql(
                "SELECT id,status FROM ai_chat_tool_calls ORDER BY id"
            ).all()
            table_sql = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='ai_chat_tool_calls'"
            ).scalar_one()
        assert values == [
            (1, "validated"),
            (2, "received"),
            (3, "awaiting_approval"),
            (4, "resolved"),
        ]
        assert "'approved'" in table_sql
        assert "status != 'resolved' OR tool_result IS NOT NULL" in table_sql
        with engine.connect() as connection:
            indexes = {
                row[1]: row
                for row in connection.exec_driver_sql(
                    "PRAGMA index_list(ai_chat_tool_calls)"
                ).all()
            }
            assert {
                "ix_ai_chat_tool_calls_conversation_id",
                "ix_ai_chat_tool_calls_run_id",
                "ix_ai_chat_tool_calls_status",
                "ix_ai_chat_tool_calls_delivery_status",
                "ux_ai_chat_tool_run_index",
                "ux_ai_chat_tool_provider_call",
            } <= set(indexes)
            assert indexes["ux_ai_chat_tool_run_index"][2] == 1
            assert indexes["ux_ai_chat_tool_provider_call"][2] == 1
            assert indexes["ux_ai_chat_tool_provider_call"][4] == 1

            def index_columns(name: str) -> tuple[str, ...]:
                return tuple(
                    row[2]
                    for row in connection.exec_driver_sql(
                        f"PRAGMA index_info({name})"
                    ).all()
                )

            assert index_columns("ix_ai_chat_tool_calls_conversation_id") == (
                "conversation_id",
            )
            assert index_columns("ix_ai_chat_tool_calls_run_id") == ("run_id",)
            assert index_columns("ix_ai_chat_tool_calls_status") == ("status",)
            assert index_columns("ix_ai_chat_tool_calls_delivery_status") == (
                "delivery_status",
            )
            assert index_columns("ux_ai_chat_tool_run_index") == (
                "run_id",
                "tool_call_index",
            )
            assert index_columns("ux_ai_chat_tool_provider_call") == (
                "run_id",
                "provider_tool_call_id",
            )
            assert any(
                index_columns(name) == ("conversation_id", "client_resolution_id")
                and row[2] == 1
                for name, row in indexes.items()
            )
    finally:
        engine.dispose()


@pytest.mark.skipif(ToolLifecycle is None, reason="legacy lifecycle is removed")
async def test_migrated_validated_call_replays_without_regenerating_payloads(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "legacy-validated-replay.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "CREATE TABLE ai_chat_tool_calls ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL, "
            "run_id INTEGER NOT NULL, tool_call_index INTEGER NOT NULL, "
            "provider_tool_call_id VARCHAR(200), tool_name VARCHAR(160) NOT NULL, "
            "arguments JSON NOT NULL, proposal_payload JSON, guard_payload JSON, "
            "status VARCHAR(24) NOT NULL, decision VARCHAR(16), tool_result JSON, "
            "delivery_status VARCHAR(16), client_resolution_id VARCHAR(160), "
            "resolved_at VARCHAR, created_at VARCHAR NOT NULL, updated_at VARCHAR NOT NULL);"
        )
        connection.execute(
            "INSERT INTO ai_chat_tool_calls "
            "(conversation_id,run_id,tool_call_index,tool_name,arguments,"
            "proposal_payload,guard_payload,status,created_at,updated_at) "
            "VALUES (1,1,0,'demo','{\"value\":\"input\"}',"
            "'{\"proposal\":1}','{\"guard\":1}','received','now','now')"
        )
    engine = create_engine(f"sqlite:///{path}")
    try:
        migrate_ai_chat_tool_call_state(engine)
    finally:
        engine.dispose()

    database = Database(path)
    monkeypatch.setattr("app.database.db", database)
    try:
        assert ToolLifecycle is not None
        dispatched = await ToolLifecycle(RepositoryFactory()).receive(
            context=ToolContext(
                conversation_id=1,
                run_id=1,
                subject={"type": "test", "id": "1"},
                scope={"field": "test"},
            ),
            call=AssembledToolCall(
                index=0,
                provider_id=None,
                name="demo",
                arguments={"value": "input"},
            ),
            handlers={"demo": _NeverInvokeHandler()},
        )
        assert isinstance(dispatched, ApprovalRequired)
        assert dispatched.proposal_payload == {"proposal": 1}
        async with database.session() as session:
            row = await RepositoryFactory().create(session).tool_calls.get(1)
        assert row is not None
        assert row.status == "awaiting_approval"
        assert row.guard_payload == {"guard": 1}
    finally:
        await database.close()


async def test_claim_execution_retries_after_rollback_and_resolves(isolated_db) -> None:
    async with isolated_db.session() as session:
        repositories = RepositoryFactory().create(session)
        conversation = await repositories.conversations.create(
            adapter="test",
            subject={"type": "test", "id": "1"},
            scope={"field": "test"},
            language="zh",
        )
        run = await repositories.runs.create(
            conversation_id=conversation.id,
            kind="user_turn",
            tools_enabled=True,
        )
        row = await repositories.tool_calls.create(
            conversation_id=conversation.id,
            run_id=run.id,
            tool_call_index=0,
            provider_tool_call_id=None,
            tool_name="demo",
            arguments={},
        )
        await session.commit()

    async with isolated_db.session() as session:
        repository = RepositoryFactory().create(session).tool_calls
        assert await repository.claim_execution(row.id) is True
        await session.rollback()

    async with isolated_db.session() as session:
        repository = RepositoryFactory().create(session).tool_calls
        persisted = await repository.get(row.id)
        assert persisted is not None
        assert persisted.status == "received"
        assert await repository.claim_execution(row.id) is True
        await repository.resolve(
            persisted,
            decision=None,
            tool_result={"outcome": "done"},
        )
        await session.commit()

    async with isolated_db.session() as session:
        persisted = await RepositoryFactory().create(session).tool_calls.get(row.id)
    assert persisted is not None
    assert persisted.status == "resolved"
    assert persisted.tool_result == {"outcome": "done"}


async def test_resolved_call_without_result_is_rejected_by_database(isolated_db) -> None:
    async with isolated_db.session() as session:
        repositories = RepositoryFactory().create(session)
        conversation = await repositories.conversations.create(
            adapter="test",
            subject={"type": "test", "id": "1"},
            scope={"field": "test"},
            language="zh",
        )
        run = await repositories.runs.create(
            conversation_id=conversation.id,
            kind="user_turn",
            tools_enabled=True,
        )
        session.add(
            AiChatToolCall(
                conversation_id=conversation.id,
                run_id=run.id,
                tool_call_index=0,
                provider_tool_call_id=None,
                tool_name="demo",
                arguments={},
                status="resolved",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
