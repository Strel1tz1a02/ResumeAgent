"""Tests for durable AI Chat Tool Call persistence."""

import asyncio
import sqlite3

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError

from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.models import AiChatToolCall
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.services.tool_call_service import ToolCallService
from app.ai_chat.tools.buffer import AssembledToolCall
from app.ai_chat.tools.handler import ToolContext, ToolHandler
from app.ai_chat.tools.results import (
    ApprovalProposal,
    ApprovalRequest,
    ApprovedToolCall,
    CompletedToolCall,
    PreparedToolCall,
    ToolResult,
    ToolInvocationResult,
    ToolValidationResult,
    ValidatedToolCall,
)
from app.ai_chat.tools.security import ToolSecurity
from app.database import Database
from app.scripts.migrate_ai_chat_tool_call_state import (
    migrate as migrate_ai_chat_tool_call_state,
)


class _DemoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class _DemoHandler(ToolHandler):
    name = "demo"
    description = "demo"
    arguments_schema = _DemoArguments
    security = ToolSecurity.MEDIUM

    def __init__(self) -> None:
        self.validation_count = 0
        self.execution_count = 0

    async def validation(self, context, arguments):  # type: ignore[no-untyped-def]
        self.validation_count += 1
        values = self.arguments_schema.model_validate(arguments)
        if values.value == "done":
            return self.show_result({"outcome": "no_change"})
        return ValidatedToolCall(
            proposal_payload={"value": values.value},
            guard_payload={"trusted": values.value},
        )

    async def execute(self, context, proposal_payload, guard_payload):  # type: ignore[no-untyped-def]
        self.execution_count += 1
        return self.show_result({"outcome": "applied"})

    def show_result(self, payload):  # type: ignore[no-untyped-def]
        return ToolResult(dict(payload))


class _NeverValidateHandler(_DemoHandler):
    async def validation(self, context, arguments):  # type: ignore[no-untyped-def]
        raise AssertionError("validated replay must not regenerate trusted payloads")


class _LegacyHandler(ToolHandler):
    """Old invoke/resolve handlers remain import-safe during staged migration."""

    name = "legacy"
    description = "legacy"
    arguments_schema = _DemoArguments
    security = ToolSecurity.MEDIUM

    async def invoke(self, context, arguments):  # type: ignore[no-untyped-def]
        values = self.arguments_schema.model_validate(arguments)
        return ApprovalProposal(
            proposal_payload={"value": values.value},
            guard_payload={"trusted": values.value},
        )

    async def resolve(self, context, proposal_payload, guard_payload, decision):  # type: ignore[no-untyped-def]
        return ToolResult({"outcome": decision})


class _ConcurrentDemoHandler(_DemoHandler):
    def __init__(self, *, immediate: bool) -> None:
        super().__init__()
        self.immediate = immediate
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def validation(self, context, arguments):  # type: ignore[no-untyped-def]
        self.validation_count += 1
        sequence = self.validation_count
        values = self.arguments_schema.model_validate(arguments)
        if sequence == 2:
            self.entered.set()
        await self.release.wait()
        if self.immediate:
            return self.show_result({"outcome": f"immediate-{sequence}"})
        return ValidatedToolCall(
            proposal_payload={"value": f"{values.value}-{sequence}"},
            guard_payload={"trusted": f"{values.value}-{sequence}"},
        )


def _tool_context(conversation_id: int, run_id: int) -> ToolContext:
    return ToolContext(
        conversation_id=conversation_id,
        run_id=run_id,
        subject={"type": "test", "id": "1"},
        scope={"field": "test"},
    )


def _tool_call(*, value: str, index: int = 0) -> AssembledToolCall:
    return AssembledToolCall(
        index=index,
        provider_id=f"provider-{index}",
        name="demo",
        arguments={"value": value},
    )


def _tool_service(isolated_db, handler: ToolHandler) -> ToolCallService:  # type: ignore[no-untyped-def]
    return ToolCallService(
        session_factory=isolated_db.session,
        repositories=RepositoryFactory(),
    ).bind_handlers({handler.name: handler})

async def _create_conversation_run(isolated_db) -> tuple[int, int]:  # type: ignore[no-untyped-def]
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
        await session.commit()
        return conversation.id, run.id


async def _create_received_tool_call(isolated_db) -> int:  # type: ignore[no-untyped-def]
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.create(
            conversation_id=conversation_id,
            run_id=run_id,
            tool_call_index=0,
            provider_tool_call_id="provider-a",
            tool_name="demo",
            arguments={"value": "same"},
        )
        await session.commit()
        return row.id


async def test_bind_handlers_returns_an_isolated_immutable_service(isolated_db) -> None:
    handler = _DemoHandler()
    service = ToolCallService(
        session_factory=isolated_db.session,
        repositories=RepositoryFactory(),
    )

    bound = service.bind_handlers({"demo": handler})

    assert dict(service.model_handlers) == {}
    assert dict(bound.model_handlers) == {"demo": handler}
    with pytest.raises(TypeError):
        service.model_handlers["other"] = handler  # type: ignore[index]
    with pytest.raises(TypeError):
        bound.model_handlers["other"] = handler  # type: ignore[index]


async def test_legacy_handler_protocol_remains_instantiable_during_migration(
    isolated_db,
) -> None:
    handler = _LegacyHandler()

    validation = await handler.validation(
        _tool_context(1, 1), {"value": "input"}
    )

    assert isinstance(validation, ValidatedToolCall)
    assert validation.proposal_payload == {"value": "input"}
    assert ToolInvocationResult is ToolValidationResult


async def test_validate_call_rejects_unknown_handler_before_materializing(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    service = _tool_service(isolated_db, _DemoHandler()).bind_handlers({})

    with pytest.raises(ToolProtocolError, match="Unknown tool: unknown"):
        await service.validate_call(
            _tool_context(conversation_id, run_id),
            AssembledToolCall(
                index=0,
                provider_id="provider-0",
                name="unknown",
                arguments={"value": "input"},
            ),
        )

    async with isolated_db.session() as session:
        count = await session.scalar(select(func.count()).select_from(AiChatToolCall))
    assert count == 0


async def test_validate_call_keeps_invalid_arguments_as_durable_received_row(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    service = _tool_service(isolated_db, _DemoHandler())

    with pytest.raises(ValueError):
        await service.validate_call(
            _tool_context(conversation_id, run_id),
            AssembledToolCall(
                index=0,
                provider_id="provider-0",
                name="demo",
                arguments={"unexpected": "value"},
            ),
        )

    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get_by_run_index(
            run_id, 0
        )
    assert row is not None
    assert row.status == "received"
    assert row.arguments == {"unexpected": "value"}


async def test_validate_call_prepares_and_persists_trusted_payloads(isolated_db) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    handler = _DemoHandler()
    dispatched = await _tool_service(isolated_db, handler).validate_call(
        _tool_context(conversation_id, run_id), _tool_call(value="input")
    )

    assert isinstance(dispatched, PreparedToolCall)
    assert dispatched.tool_name == "demo"
    assert dispatched.security is ToolSecurity.MEDIUM
    assert handler.validation_count == 1
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get(
            dispatched.tool_call_id
        )
    assert row is not None
    assert row.status == "validated"
    assert row.proposal_payload == {"value": "input"}
    assert row.guard_payload == {"trusted": "input"}


async def test_validate_call_resolves_immediate_result_before_returning(isolated_db) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    dispatched = await _tool_service(isolated_db, _DemoHandler()).validate_call(
        _tool_context(conversation_id, run_id), _tool_call(value="done")
    )

    assert isinstance(dispatched, CompletedToolCall)
    assert dispatched.result == {"outcome": "no_change"}
    assert dispatched.decision is None
    assert dispatched.replayed is False
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get(
            dispatched.tool_call_id
        )
    assert row is not None
    assert row.status == "resolved"
    assert row.tool_result == {"outcome": "no_change"}


async def test_validate_call_replay_does_not_validate_twice(isolated_db) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    handler = _DemoHandler()
    service = _tool_service(isolated_db, handler)

    first = await service.validate_call(
        _tool_context(conversation_id, run_id), _tool_call(value="input")
    )
    replay = await service.validate_call(
        _tool_context(conversation_id, run_id), _tool_call(value="input")
    )

    assert isinstance(first, PreparedToolCall)
    assert isinstance(replay, PreparedToolCall)
    assert replay.tool_call_id == first.tool_call_id
    assert handler.validation_count == 1


async def test_validate_call_concurrent_prepared_validation_keeps_one_durable_winner(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    handler = _ConcurrentDemoHandler(immediate=False)
    service = _tool_service(isolated_db, handler)
    context = _tool_context(conversation_id, run_id)
    call = _tool_call(value="input")
    first = asyncio.create_task(service.validate_call(context, call))
    second = asyncio.create_task(service.validate_call(context, call))
    await asyncio.wait_for(handler.entered.wait(), timeout=2)
    handler.release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert isinstance(first_result, PreparedToolCall)
    assert isinstance(second_result, PreparedToolCall)
    assert first_result.tool_call_id == second_result.tool_call_id
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get(
            first_result.tool_call_id
        )
    assert row is not None
    assert row.status == "validated"
    assert row.proposal_payload in ({"value": "input-1"}, {"value": "input-2"})
    assert row.guard_payload == {"trusted": row.proposal_payload["value"]}


async def test_validate_call_concurrent_immediate_result_replays_durable_winner(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    handler = _ConcurrentDemoHandler(immediate=True)
    service = _tool_service(isolated_db, handler)
    context = _tool_context(conversation_id, run_id)
    call = _tool_call(value="done")
    first = asyncio.create_task(service.validate_call(context, call))
    second = asyncio.create_task(service.validate_call(context, call))
    await asyncio.wait_for(handler.entered.wait(), timeout=2)
    handler.release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert isinstance(first_result, CompletedToolCall)
    assert isinstance(second_result, CompletedToolCall)
    assert first_result.tool_call_id == second_result.tool_call_id
    assert first_result.result == second_result.result
    assert first_result.result in (
        {"outcome": "immediate-1"},
        {"outcome": "immediate-2"},
    )
    assert sorted((first_result.replayed, second_result.replayed)) == [False, True]
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get(
            first_result.tool_call_id
        )
    assert row is not None
    assert row.status == "resolved"
    assert row.tool_result == first_result.result


@pytest.mark.parametrize(
    ("status", "expected_type"),
    [
        ("awaiting_approval", ApprovalRequest),
        ("approved", ApprovedToolCall),
        ("resolved", CompletedToolCall),
    ],
)
async def test_validate_call_maps_existing_durable_status(
    isolated_db, status: str, expected_type: type[object]
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    handler = _DemoHandler()
    service = _tool_service(isolated_db, handler)
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.create(
            conversation_id=conversation_id,
            run_id=run_id,
            tool_call_index=0,
            provider_tool_call_id="provider-0",
            tool_name="demo",
            arguments={"value": "input"},
        )
        row.proposal_payload = {"value": "input"}
        row.guard_payload = {"trusted": "input"}
        row.status = status
        if status == "approved":
            row.client_resolution_id = "approved-1"
            row.decision = "approve"
        if status == "resolved":
            row.tool_result = {"outcome": "done"}
            row.decision = "reject"
            row.client_resolution_id = "rejected-1"
        await session.commit()

    dispatched = await service.validate_call(
        _tool_context(conversation_id, run_id), _tool_call(value="input")
    )

    assert isinstance(dispatched, expected_type)
    assert handler.validation_count == 0
    if status == "awaiting_approval":
        assert dispatched.proposal_payload == {"value": "input"}  # type: ignore[union-attr]
    elif status == "approved":
        assert dispatched.client_resolution_id == "approved-1"  # type: ignore[union-attr]
    else:
        assert dispatched.result == {"outcome": "done"}  # type: ignore[union-attr]
        assert dispatched.decision == "reject"  # type: ignore[union-attr]
        assert dispatched.replayed is True  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("status", "decision", "client_resolution_id"),
    [
        ("approved", "approve", ""),
        ("resolved", "reject", None),
    ],
)
async def test_validate_call_rejects_durable_decision_without_resolution_token(
    isolated_db,
    status: str,
    decision: str,
    client_resolution_id: str | None,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.create(
            conversation_id=conversation_id,
            run_id=run_id,
            tool_call_index=0,
            provider_tool_call_id="provider-0",
            tool_name="demo",
            arguments={"value": "input"},
        )
        row.proposal_payload = {"value": "input"}
        row.guard_payload = {"trusted": "input"}
        row.status = status
        row.decision = decision
        row.client_resolution_id = client_resolution_id
        if status == "resolved":
            row.tool_result = {"outcome": "done"}
        await session.commit()

    with pytest.raises(ToolProtocolError, match="identity"):
        await _tool_service(isolated_db, _DemoHandler()).validate_call(
            _tool_context(conversation_id, run_id), _tool_call(value="input")
        )


async def test_materialize_is_atomic_under_concurrent_replay(isolated_db) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)

    async def worker(provider_id: str) -> int:
        async with isolated_db.session() as session:
            row = await RepositoryFactory().create(session).tool_calls.materialize(
                conversation_id=conversation_id,
                run_id=run_id,
                tool_call_index=0,
                provider_tool_call_id=provider_id,
                tool_name="demo",
                arguments={"value": "same"},
            )
            await session.commit()
            return row.id

    first, second = await asyncio.gather(worker("provider-a"), worker("provider-b"))

    assert first == second
    async with isolated_db.session() as session:
        repository = RepositoryFactory().create(session).tool_calls
        with pytest.raises(ToolProtocolError, match="index was reused inconsistently"):
            await repository.materialize(
                conversation_id=conversation_id,
                run_id=run_id,
                tool_call_index=0,
                provider_tool_call_id="provider-c",
                tool_name="demo",
                arguments={"value": "different"},
            )


async def test_materialize_converts_provider_identity_conflict_to_protocol_error(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    async with isolated_db.session() as session:
        repository = RepositoryFactory().create(session).tool_calls
        await repository.materialize(
            conversation_id=conversation_id,
            run_id=run_id,
            tool_call_index=0,
            provider_tool_call_id="provider-a",
            tool_name="demo",
            arguments={"value": "same"},
        )
        await session.commit()

    async with isolated_db.session() as session:
        repository = RepositoryFactory().create(session).tool_calls
        with pytest.raises(ToolProtocolError, match="index was reused inconsistently"):
            await repository.materialize(
                conversation_id=conversation_id,
                run_id=run_id,
                tool_call_index=1,
                provider_tool_call_id="provider-a",
                tool_name="demo",
                arguments={"value": "same"},
            )


async def test_materialize_rejects_crossed_index_and_provider_conflicts(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    async with isolated_db.session() as session:
        repository = RepositoryFactory().create(session).tool_calls
        await repository.materialize(
            conversation_id=conversation_id,
            run_id=run_id,
            tool_call_index=0,
            provider_tool_call_id="provider-a",
            tool_name="demo",
            arguments={"value": "same"},
        )
        await repository.materialize(
            conversation_id=conversation_id,
            run_id=run_id,
            tool_call_index=1,
            provider_tool_call_id="provider-b",
            tool_name="demo",
            arguments={"value": "same"},
        )
        await session.commit()

    async with isolated_db.session() as session:
        repository = RepositoryFactory().create(session).tool_calls
        with pytest.raises(ToolProtocolError, match="index was reused inconsistently"):
            await repository.materialize(
                conversation_id=conversation_id,
                run_id=run_id,
                tool_call_index=1,
                provider_tool_call_id="provider-a",
                tool_name="demo",
                arguments={"value": "same"},
            )


async def test_repository_transition_persists_approval_before_execution(
    isolated_db,
) -> None:
    tool_call_id = await _create_received_tool_call(isolated_db)

    async with isolated_db.session() as session:
        repository = RepositoryFactory().create(session).tool_calls
        row = await repository.get(tool_call_id)
        assert row is not None
        assert await repository.save_validation(
            row,
            proposal_payload={"proposal": "trusted"},
            guard_payload={"guard": "trusted"},
        ) is True
        assert row.status == "validated"
        assert await repository.save_validation(
            row,
            proposal_payload={"proposal": "replacement"},
            guard_payload={"guard": "replacement"},
        ) is False
        assert row.proposal_payload == {"proposal": "trusted"}
        assert await repository.claim_approval_request(tool_call_id) is True
        assert await repository.claim_approval_request(tool_call_id) is False
        assert await repository.approve(tool_call_id, "approve-1") is True
        assert await repository.approve(tool_call_id, "approve-1") is False
        assert await repository.claim_execution(
            tool_call_id, from_status="approved"
        ) is True
        assert await repository.claim_execution(
            tool_call_id, from_status="approved"
        ) is False
        await repository.resolve(
            row,
            decision="approve",
            tool_result={"outcome": "done"},
            client_resolution_id="approve-1",
        )
        await session.commit()

    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get(tool_call_id)
    assert row is not None
    assert row.status == "resolved"
    assert row.decision == "approve"
    assert row.client_resolution_id == "approve-1"
    assert row.tool_result == {"outcome": "done"}
    assert row.delivery_status == "pending"


async def test_repository_transition_rejects_or_executes_validated_call_once(
    isolated_db,
) -> None:
    rejected_id = await _create_received_tool_call(isolated_db)
    direct_id = await _create_received_tool_call(isolated_db)

    async with isolated_db.session() as session:
        repository = RepositoryFactory().create(session).tool_calls
        for tool_call_id in (rejected_id, direct_id):
            row = await repository.get(tool_call_id)
            assert row is not None
            await repository.save_validation(
                row,
                proposal_payload={"proposal": "trusted"},
                guard_payload={"guard": "trusted"},
            )
        assert await repository.claim_approval_request(rejected_id) is True
        assert await repository.claim_rejection(rejected_id, "reject-1") is True
        assert await repository.claim_rejection(rejected_id, "reject-1") is False
        assert await repository.claim_execution(
            direct_id, from_status="validated"
        ) is True
        assert await repository.claim_execution(
            direct_id, from_status="validated"
        ) is False
        await session.commit()

    async with isolated_db.session() as session:
        repository = RepositoryFactory().create(session).tool_calls
        rejected = await repository.get(rejected_id)
        direct = await repository.get(direct_id)
    assert rejected is not None
    assert rejected.status == "executing"
    assert rejected.decision == "reject"
    assert rejected.client_resolution_id == "reject-1"
    assert direct is not None
    assert direct.status == "executing"


async def test_legacy_claim_resolution_keeps_result_write_in_same_transaction(
    isolated_db,
) -> None:
    tool_call_id = await _create_received_tool_call(isolated_db)

    async with isolated_db.session() as session:
        repository = RepositoryFactory().create(session).tool_calls
        row = await repository.get(tool_call_id)
        assert row is not None
        await repository.request_approval(
            row,
            proposal_payload={"proposal": "trusted"},
            guard_payload={"guard": "trusted"},
        )
        assert await repository.claim_resolution(
            tool_call_id,
            decision="reject",
            client_resolution_id="legacy-reject-1",
        ) is True
        assert row.status == "executing"
        assert row.decision == "reject"
        assert row.client_resolution_id == "legacy-reject-1"
        await repository.resolve(
            row,
            decision="reject",
            tool_result={"outcome": "rejected"},
            client_resolution_id="legacy-reject-1",
        )
        await session.commit()

    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get(tool_call_id)
    assert row is not None
    assert row.status == "resolved"
    assert row.tool_result == {"outcome": "rejected"}


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


async def test_migrated_validated_call_replays_without_regenerating_payloads(
    tmp_path,
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
    try:
        dispatched = await ToolCallService(
            session_factory=database.session,
            repositories=RepositoryFactory(),
        ).bind_handlers({"demo": _NeverValidateHandler()}).validate_call(
            _tool_context(
                conversation_id=1,
                run_id=1,
            ),
            AssembledToolCall(
                index=0,
                provider_id=None,
                name="demo",
                arguments={"value": "input"},
            ),
        )
        assert isinstance(dispatched, PreparedToolCall)
        assert dispatched.security is ToolSecurity.MEDIUM
        async with database.session() as session:
            row = await RepositoryFactory().create(session).tool_calls.get(1)
        assert row is not None
        assert row.status == "validated"
        assert row.proposal_payload == {"proposal": 1}
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
        assert await repositories.tool_calls.save_validation(
            row,
            proposal_payload={"proposal": "trusted"},
            guard_payload={"guard": "trusted"},
        ) is True
        await session.commit()

    async with isolated_db.session() as session:
        repository = RepositoryFactory().create(session).tool_calls
        assert await repository.claim_execution(row.id, from_status="validated") is True
        await session.rollback()

    async with isolated_db.session() as session:
        repository = RepositoryFactory().create(session).tool_calls
        persisted = await repository.get(row.id)
        assert persisted is not None
        assert persisted.status == "validated"
        assert await repository.claim_execution(row.id, from_status="validated") is True
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
