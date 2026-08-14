"""AI Chat 工具调用持久化测试。"""

import asyncio
import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from app.ai_chat.errors import IdempotencyConflictError, ToolProtocolError
from app.ai_chat.models import AiChatMessage, AiChatToolCall
from app.ai_chat.repositories import (
    AiChatRepositories,
    RepositoryFactory,
    ToolCallRepository,
)
from app.ai_chat.services.tool_call_service import ToolCallService
from app.ai_chat.streaming.model import build_model_tools
from app.ai_chat.tools.buffer import encode_tool_call
from app.ai_chat.tools.handler import ToolHandler
from app.ai_chat.tools.security import ToolSecurity
from app.ai_chat.tools.types import ToolCall, ToolContext, ToolResult
from app.database import Database
from app.scripts.migrate_ai_chat_tool_call_state import (
    migrate as migrate_ai_chat_tool_call_state,
)
from app.scripts.migrate_ai_chat_tool_input_state import (
    migrate as migrate_ai_chat_tool_input_state,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError


class _DemoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class _FloatArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float


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
        return {"value": values.value}, {"trusted": values.value}

    async def execute(  # type: ignore[no-untyped-def]
        self, context, proposal_payload, guard_payload
    ):
        self.execution_count += 1
        return self.show_result({"outcome": "applied"})

    def show_result(self, payload):  # type: ignore[no-untyped-def]
        return ToolResult(dict(payload))


class _FloatHandler(_DemoHandler):
    name = "float_demo"
    arguments_schema = _FloatArguments


class _InternalHandler(_DemoHandler):
    name = "internal"
    model_visible = False
    deliver_result_to_model = False


class _NeverValidateHandler(_DemoHandler):
    async def validation(self, context, arguments):  # type: ignore[no-untyped-def]
        raise AssertionError("validated replay must not regenerate trusted payloads")


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
        return (
            {"value": f"{values.value}-{sequence}"},
            {"trusted": f"{values.value}-{sequence}"},
        )


class _ExecutionHandler(_DemoHandler):
    security = ToolSecurity.LOW

    def __init__(self, *, fail_first: bool = False) -> None:
        super().__init__()
        self.fail_first = fail_first
        self.success_count = 0
        self.received_payloads: list[tuple[dict[str, object], dict[str, object]]] = []

    async def execute(  # type: ignore[no-untyped-def]
        self, context, proposal_payload, guard_payload
    ):
        self.execution_count += 1
        self.received_payloads.append(
            (dict(proposal_payload), dict(guard_payload))
        )
        assert context.session is not None
        context.session.add(
            AiChatMessage(
                conversation_id=context.conversation_id,
                run_id=context.run_id,
                sequence=1,
                role="assistant",
                content="tool side effect",
                status="completed",
            )
        )
        if self.fail_first and self.execution_count == 1:
            raise RuntimeError("transient execution failure")
        self.success_count += 1
        return self.show_result(
            {
                "outcome": "applied",
                "proposal": dict(proposal_payload),
                "guard": dict(guard_payload),
            }
        )


class _BlockingExecutionHandler(_ExecutionHandler):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, context, proposal_payload, guard_payload):  # type: ignore[no-untyped-def]
        self.execution_count += 1
        self.received_payloads.append(
            (dict(proposal_payload), dict(guard_payload))
        )
        self.entered.set()
        await self.release.wait()
        assert context.session is not None
        context.session.add(
            AiChatMessage(
                conversation_id=context.conversation_id,
                run_id=context.run_id,
                sequence=1,
                role="assistant",
                content="tool side effect",
                status="completed",
            )
        )
        self.success_count += 1
        return self.show_result({"outcome": "applied-once"})


class _TwoPartyBarrier:
    def __init__(self) -> None:
        self.arrivals = 0
        self.ready = asyncio.Event()

    async def wait(self) -> None:
        self.arrivals += 1
        if self.arrivals == 2:
            self.ready.set()
        await self.ready.wait()


@dataclass
class _RepositoryObservation:
    approval_barrier: _TwoPartyBarrier | None = None
    resolution_barrier: _TwoPartyBarrier | None = None
    resolution_id: str | None = None
    execution_claims: int = 0
    second_execution_claim: asyncio.Event | None = None


class _ObservedToolCallRepository(ToolCallRepository):
    def __init__(self, session, observation):  # type: ignore[no-untyped-def]
        super().__init__(session)
        self._observation = observation

    async def claim_approval_request(self, tool_call_id: int) -> bool:
        barrier = self._observation.approval_barrier
        if barrier is not None:
            await barrier.wait()
        return await super().claim_approval_request(tool_call_id)

    async def get_by_resolution_id(
        self, conversation_id: int, client_resolution_id: str
    ):
        row = await super().get_by_resolution_id(
            conversation_id, client_resolution_id
        )
        barrier = self._observation.resolution_barrier
        if (
            row is None
            and barrier is not None
            and client_resolution_id == self._observation.resolution_id
        ):
            await barrier.wait()
        return row

    async def claim_execution(self, tool_call_id: int, *, from_status):  # type: ignore[no-untyped-def]
        self._observation.execution_claims += 1
        if (
            self._observation.execution_claims == 2
            and self._observation.second_execution_claim is not None
        ):
            self._observation.second_execution_claim.set()
        return await super().claim_execution(
            tool_call_id, from_status=from_status
        )


class _ObservedRepositoryFactory(RepositoryFactory):
    def __init__(self, observation: _RepositoryObservation) -> None:
        self._observation = observation

    def create(self, session):  # type: ignore[no-untyped-def]
        repositories = super().create(session)
        return AiChatRepositories(
            conversations=repositories.conversations,
            messages=repositories.messages,
            runs=repositories.runs,
            tool_calls=_ObservedToolCallRepository(session, self._observation),
        )


def _tool_context(conversation_id: int, run_id: int) -> ToolContext:
    return ToolContext(
        conversation_id=conversation_id,
        run_id=run_id,
        subject={"type": "test", "id": "1"},
        scope={"field": "test"},
    )


def _tool_call(*, value: str, index: int = 0) -> str:
    return encode_tool_call(
        index=index,
        provider_id=f"provider-{index}",
        name="demo",
        arguments=json.dumps({"value": value}),
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


async def test_external_input_call_resolves_once_and_replays(isolated_db) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    service = _tool_service(isolated_db, _DemoHandler())
    call = await service.validate_call(
        _tool_context(conversation_id, run_id),
        _tool_call(value="question-batch"),
    )
    waiting = await service.request_input(call["tool_call_id"])
    assert waiting["status"] == "awaiting_input"
    assert (await service.find_awaiting_input(run_id))["tool_call_id"] == call[  # type: ignore[index]
        "tool_call_id"
    ]

    first = await service.resolve_input(
        call["tool_call_id"], "client-input-1", {"answers": [{"value": "Python"}]}
    )
    replay = await service.resolve_input(
        call["tool_call_id"], "client-input-1", {"answers": [{"value": "Python"}]}
    )
    assert first.replayed is False
    assert replay.replayed is True
    assert replay.payload == {"answers": [{"value": "Python"}]}
    assert await service.find_awaiting_input(run_id) is None

    with pytest.raises(IdempotencyConflictError):
        await service.resolve_input(
            call["tool_call_id"], "client-input-2", {"answers": [{"value": "Java"}]}
        )
    with pytest.raises(IdempotencyConflictError):
        await service.resolve_input(
            call["tool_call_id"], "client-input-1", {"answers": [{"value": True}]}
        )


async def test_system_call_uses_stable_identity_and_allocates_indexes(isolated_db) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    service = _tool_service(isolated_db, _DemoHandler())
    context = _tool_context(conversation_id, run_id)
    first = await service.validate_system_call(
        context,
        identity="jd-import:persist:jd-1",
        name="demo",
        arguments={"value": "one"},
    )
    replay = await service.validate_system_call(
        context,
        identity="jd-import:persist:jd-1",
        name="demo",
        arguments={"value": "one"},
    )
    second = await service.validate_system_call(
        context,
        identity="jd-import:persist:jd-2",
        name="demo",
        arguments={"value": "two"},
    )
    assert replay["tool_call_id"] == first["tool_call_id"]
    assert replay["replayed"] is True
    assert second["index"] == first["index"] + 1
    with pytest.raises(IdempotencyConflictError):
        await service.validate_system_call(
            context,
            identity="jd-import:persist:jd-1",
            name="demo",
            arguments={"value": "changed"},
        )


async def test_model_call_as_discards_model_identity(isolated_db) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    service = _tool_service(isolated_db, _DemoHandler())
    call = await service.validate_model_call_as(
        _tool_context(conversation_id, run_id),
        encode_tool_call(
            index=999,
            provider_id="untrusted-provider-id",
            name="demo",
            arguments=json.dumps({"value": "question"}),
        ),
        identity="jd-import:questions:1",
        expected_name="demo",
    )
    assert call["index"] == 0
    assert call["provider_id"] == "jd-import:questions:1"


def test_tool_input_state_migration_is_idempotent(tmp_path) -> None:
    path = tmp_path / "tool-input-state.db"
    database = Database(path)
    database._ensure_initialized()
    assert database._sync_engine is not None
    migrate_ai_chat_tool_input_state(database._sync_engine)
    migrate_ai_chat_tool_input_state(database._sync_engine)
    with database._sync_engine.connect() as connection:
        table_sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='ai_chat_tool_calls'"
        ).scalar_one()
    assert "'awaiting_input'" in table_sql


def test_handler_capabilities_control_model_schema_and_delivery() -> None:
    visible = _DemoHandler()
    internal = _InternalHandler()
    definitions = build_model_tools({visible.name: visible, internal.name: internal})
    assert [item["function"]["name"] for item in definitions] == ["demo"]
    assert visible.deliver_result_to_model is True
    assert internal.deliver_result_to_model is False


def test_tool_handler_requires_each_business_method() -> None:
    """Tool Handler 缺少任一业务方法时都不能注册实例。"""

    class CompleteHandler(ToolHandler):
        name = "complete"
        description = "complete"
        arguments_schema = _DemoArguments
        security = ToolSecurity.LOW

        async def validation(self, context, arguments):  # type: ignore[no-untyped-def]
            return self.show_result({"outcome": "validated"})

        async def execute(  # type: ignore[no-untyped-def]
            self, context, proposal_payload, guard_payload
        ):
            return self.show_result({"outcome": "executed"})

        def show_result(self, payload):  # type: ignore[no-untyped-def]
            return ToolResult(dict(payload))

    required = {"validation", "execute", "show_result"}
    assert ToolHandler.__abstractmethods__ == required
    for missing in required:
        methods = {
            name: CompleteHandler.__dict__[name]
            for name in required - {missing}
        }
        incomplete = type(
            f"Missing{missing.title()}Handler",
            (ToolHandler,),
            {
                "name": f"missing_{missing}",
                "description": "incomplete",
                "arguments_schema": _DemoArguments,
                "security": ToolSecurity.LOW,
                **methods,
            },
        )
        with pytest.raises(TypeError, match="abstract"):
            incomplete()


def test_fresh_process_exposes_only_the_unified_tool_contract() -> None:
    """全新进程导入不得因测试执行顺序继续暴露旧工具协议。"""
    backend_root = Path(__file__).resolve().parents[2]
    script = """
import importlib
from pathlib import Path

try:
    importlib.import_module("app.ai_chat.tools.lifecycle")
except ModuleNotFoundError as exc:
    assert exc.name == "app.ai_chat.tools.lifecycle"
else:
    raise AssertionError("legacy lifecycle module is still importable")

try:
    importlib.import_module("app.ai_chat.tools.results")
except ModuleNotFoundError as exc:
    assert exc.name == "app.ai_chat.tools.results"
else:
    raise AssertionError("split Tool result types are still importable")

try:
    importlib.import_module("app.ai_chat.tools.contracts")
except ModuleNotFoundError as exc:
    assert exc.name == "app.ai_chat.tools.contracts"
else:
    raise AssertionError("old Tool contracts module is still importable")

import app.ai_chat.tools as tools
import app.ai_chat.tools.types as tool_types
from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.memory import MemoryService
from app.ai_chat.repositories import RepositoryFactory, ToolCallRepository
from app.ai_chat.services import ToolCallService
from app.ai_chat.streaming.model import AiChatModel
from app.ai_chat.tools.handler import ToolHandler
from app.database import db
from app.experience import ExperienceAdapter
from app.experience.graph import build_experience_graph
from app.experience.tools.content_change import ContentChangeHandler

for alias in (
    "ApprovalProposal",
    "ToolInvocationResult",
    "ApprovalInput",
    "ApprovalRequest",
    "AssembledToolCall",
    "ApprovedToolCall",
    "CompletedToolCall",
    "PendingToolResult",
    "PreparedToolCall",
    "ToolCallState",
    "ToolValidationResult",
    "ValidatedToolCall",
):
    assert not hasattr(tool_types, alias), alias
    assert not hasattr(tools, alias), alias
for name in (
    "ApprovalAction",
    "ApprovalDecision",
    "ToolCall",
    "ToolCallStatus",
    "ToolContext",
    "ToolResult",
):
    assert getattr(tools, name) is getattr(tool_types, name), name
assert ToolHandler.__abstractmethods__ == {"validation", "execute", "show_result"}
assert not hasattr(ToolHandler, "invoke")
assert not hasattr(ToolHandler, "resolve")
assert not hasattr(ContentChangeHandler, "invoke")
assert not hasattr(ContentChangeHandler, "resolve")
assert not hasattr(ToolCallRepository, "request_approval")
assert not hasattr(ToolCallRepository, "claim_resolution")
assert set(AiChatRuntime.__dataclass_fields__) == {"model", "tools", "memory"}
assert not hasattr(AiChatRuntime, "receive_tool_call")
assert "decision" not in tool_types.ToolCall.__annotations__
assert "client_resolution_id" not in tool_types.ToolCall.__annotations__
assert {"decision", "client_resolution_id"}.issubset(
    tool_types.ApprovalDecision.__annotations__
)

service = ToolCallService(db.session, RepositoryFactory()).bind_handlers(
    ExperienceAdapter().get_tool_handlers()
)
graph = build_experience_graph(
    AiChatRuntime(AiChatModel(), service, MemoryService())
)
assert set(graph.nodes) == {"llm", "validator", "guard", "approver", "executor"}
graph.compile()

app_root = Path.cwd() / "app"
for call in ("handler.validation", "handler.execute"):
    matches = [
        path.relative_to(Path.cwd()).as_posix()
        for path in app_root.rglob("*.py")
        if call in path.read_text(encoding="utf-8")
    ]
    assert matches == ["app/ai_chat/services/tool_call_service.py"], (call, matches)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_parse_model_call_rejects_index_outside_sqlite_int64() -> None:
    maximum = ToolCallService._parse_model_call(
        encode_tool_call(
            index=(1 << 63) - 1,
            provider_id=None,
            name="demo",
            arguments="{}",
        )
    )
    assert maximum[0] == (1 << 63) - 1

    with pytest.raises(ToolProtocolError, match="index"):
        ToolCallService._parse_model_call(
            encode_tool_call(
                index=1 << 63,
                provider_id=None,
                name="demo",
                arguments="{}",
            )
        )


@pytest.mark.parametrize(
    "arguments",
    [
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"value":-Infinity}',
        '{"value":1e400}',
        '{"nested":[{"value":-1e400}]}',
    ],
)
def test_parse_model_call_rejects_non_finite_json_numbers(arguments: str) -> None:
    with pytest.raises(ToolProtocolError, match="finite|valid JSON"):
        ToolCallService._parse_model_call(
            encode_tool_call(
                index=0,
                provider_id=None,
                name="demo",
                arguments=arguments,
            )
        )


async def test_validate_call_rejects_schema_coerced_non_finite_number(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    handler = _FloatHandler()

    with pytest.raises(ToolProtocolError, match="non-finite"):
        await _tool_service(isolated_db, handler).validate_call(
            _tool_context(conversation_id, run_id),
            encode_tool_call(
                index=0,
                provider_id="provider-0",
                name=handler.name,
                arguments=json.dumps({"value": "Infinity"}),
            ),
        )

    assert handler.validation_count == 0
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get_by_run_index(
            run_id,
            0,
        )
    assert row is not None
    assert row.status == "received"
    assert row.arguments == {"value": "Infinity"}


async def test_validate_call_rejects_unknown_handler_before_materializing(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    service = _tool_service(isolated_db, _DemoHandler()).bind_handlers({})

    with pytest.raises(ToolProtocolError, match="Unknown tool: unknown"):
        await service.validate_call(
            _tool_context(conversation_id, run_id),
            encode_tool_call(
                index=0,
                provider_id="provider-0",
                name="unknown",
                arguments=json.dumps({"value": "input"}),
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

    with pytest.raises(ToolProtocolError, match="Invalid arguments"):
        await service.validate_call(
            _tool_context(conversation_id, run_id),
            encode_tool_call(
                index=0,
                provider_id="provider-0",
                name="demo",
                arguments=json.dumps({"unexpected": "value"}),
            ),
        )

    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get_by_run_index(
            run_id, 0
        )
    assert row is not None
    assert row.status == "received"
    assert row.arguments == {"unexpected": "value"}


async def test_validate_call_rejects_corrupt_received_row_before_mutation(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    handler = _DemoHandler()
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.create(
            conversation_id=conversation_id,
            run_id=run_id,
            tool_call_index=0,
            provider_tool_call_id="provider-0",
            tool_name="demo",
            arguments={"value": "input"},
        )
        row.proposal_payload = {"stale": "proposal"}
        row.guard_payload = {"stale": "guard"}
        row.decision = "approve"
        row.client_resolution_id = "orphan-resolution"
        row.tool_result = {"outcome": "stale"}
        row.delivery_status = "pending"
        row.resolved_at = "stale"
        await session.commit()
        tool_call_id = row.id

    with pytest.raises(ToolProtocolError, match="received|unexpected"):
        await _tool_service(isolated_db, handler).validate_call(
            _tool_context(conversation_id, run_id),
            _tool_call(value="input"),
        )

    assert handler.validation_count == 0
    async with isolated_db.session() as session:
        durable = await RepositoryFactory().create(session).tool_calls.get(tool_call_id)
    assert durable is not None
    assert durable.status == "received"
    assert durable.decision == "approve"
    assert durable.client_resolution_id == "orphan-resolution"
    assert durable.proposal_payload == {"stale": "proposal"}


async def test_validate_call_prepares_and_persists_trusted_payloads(isolated_db) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    handler = _DemoHandler()
    dispatched = await _tool_service(isolated_db, handler).validate_call(
        _tool_context(conversation_id, run_id), _tool_call(value="input")
    )

    assert dispatched["status"] == "validated"
    assert dispatched["name"] == "demo"
    assert dispatched["security"] == ToolSecurity.MEDIUM.value
    assert handler.validation_count == 1
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get(
            dispatched["tool_call_id"]
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

    assert dispatched["status"] == "resolved"
    assert dispatched["result"] == {"outcome": "no_change"}
    assert "decision" not in dispatched
    assert "client_resolution_id" not in dispatched
    assert dispatched["replayed"] is False
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get(
            dispatched["tool_call_id"]
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

    assert first["status"] == "validated"
    assert replay["status"] == "validated"
    assert replay["tool_call_id"] == first["tool_call_id"]
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

    assert first_result["status"] == "validated"
    assert second_result["status"] == "validated"
    assert first_result["tool_call_id"] == second_result["tool_call_id"]
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get(
            first_result["tool_call_id"]
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

    assert first_result["status"] == "resolved"
    assert second_result["status"] == "resolved"
    assert first_result["tool_call_id"] == second_result["tool_call_id"]
    assert first_result["result"] == second_result["result"]
    assert first_result["result"] in (
        {"outcome": "immediate-1"},
        {"outcome": "immediate-2"},
    )
    assert sorted((first_result["replayed"], second_result["replayed"])) == [False, True]
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get(
            first_result["tool_call_id"]
        )
    assert row is not None
    assert row.status == "resolved"
    assert row.tool_result == first_result["result"]


@pytest.mark.parametrize(
    "status",
    [
        "awaiting_approval",
        "approved",
        "resolved",
    ],
)
async def test_validate_call_maps_existing_durable_status(
    isolated_db, status: str
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
            row.delivery_status = "pending"
            row.resolved_at = "resolved-now"
        await session.commit()

    dispatched = await service.validate_call(
        _tool_context(conversation_id, run_id), _tool_call(value="input")
    )

    assert dispatched["status"] == status
    assert handler.validation_count == 0
    assert "decision" not in dispatched
    assert "client_resolution_id" not in dispatched
    if status == "awaiting_approval":
        assert dispatched["proposal_payload"] == {"value": "input"}
    elif status == "approved":
        assert dispatched["should_execute"] is True
        assert row.decision == "approve"
        assert row.client_resolution_id == "approved-1"
    else:
        assert dispatched["result"] == {"outcome": "done"}
        assert dispatched["should_execute"] is False
        assert row.decision == "reject"
        assert row.client_resolution_id == "rejected-1"
        assert dispatched["replayed"] is True


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
            row.delivery_status = "pending"
            row.resolved_at = "resolved-now"
        await session.commit()

    with pytest.raises(ToolProtocolError, match="identity"):
        await _tool_service(isolated_db, _DemoHandler()).validate_call(
            _tool_context(conversation_id, run_id), _tool_call(value="input")
        )


@pytest.mark.parametrize(
    (
        "status",
        "decision",
        "client_resolution_id",
        "proposal_payload",
        "guard_payload",
        "tool_result",
    ),
    [
        ("validated", None, None, {"value": "input"}, {"trusted": "input"}, {"outcome": "early"}),
        ("validated", "approve", "approval-1", {"value": "input"}, {"trusted": "input"}, None),
        (
            "awaiting_approval",
            "approve",
            "approval-1",
            {"value": "input"},
            {"trusted": "input"},
            None,
        ),
        ("awaiting_approval", None, "approval-1", {"value": "input"}, {"trusted": "input"}, None),
        (
            "approved",
            "approve",
            "approval-1",
            {"value": "input"},
            {"trusted": "input"},
            {"outcome": "early"},
        ),
        ("resolved", "approve", "approval-1", None, None, {"outcome": "done"}),
    ],
)
async def test_service_rejects_inconsistent_durable_tool_call_state(
    isolated_db,
    status: str,
    decision: str | None,
    client_resolution_id: str | None,
    proposal_payload: dict[str, object] | None,
    guard_payload: dict[str, object] | None,
    tool_result: dict[str, object] | None,
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
        row.status = status
        row.decision = decision
        row.client_resolution_id = client_resolution_id
        row.proposal_payload = proposal_payload
        row.guard_payload = guard_payload
        row.tool_result = tool_result
        if status == "resolved":
            row.delivery_status = "pending"
            row.resolved_at = "resolved-now"
        await session.commit()

    with pytest.raises(ToolProtocolError):
        await _tool_service(isolated_db, _DemoHandler()).validate_call(
            _tool_context(conversation_id, run_id), _tool_call(value="input")
        )


@pytest.mark.parametrize(
    ("status", "delivery_status", "resolved_at"),
    [
        ("validated", "pending", None),
        ("awaiting_approval", None, "stale"),
        ("approved", "pending", "stale"),
        ("resolved", None, "resolved-now"),
        ("resolved", "pending", None),
        ("resolved", "pending", "   "),
    ],
)
async def test_service_rejects_incomplete_lifecycle_metadata(
    isolated_db,
    status: str,
    delivery_status: str | None,
    resolved_at: str | None,
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
        row.status = status
        row.proposal_payload = {"value": "input"}
        row.guard_payload = {"trusted": "input"}
        row.delivery_status = delivery_status
        row.resolved_at = resolved_at
        if status in {"approved", "resolved"}:
            row.decision = "approve"
            row.client_resolution_id = "approval-1"
        if status == "resolved":
            row.tool_result = {"outcome": "done"}
        await session.commit()
        tool_call_id = row.id

    with pytest.raises(ToolProtocolError, match="delivery|resolved"):
        await _tool_service(isolated_db, _DemoHandler()).get_call(tool_call_id)


async def test_request_approval_is_durable_and_idempotent(isolated_db) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    service = _tool_service(isolated_db, _DemoHandler())
    prepared = await service.validate_call(
        _tool_context(conversation_id, run_id), _tool_call(value="input")
    )
    assert prepared["status"] == "validated"

    request = await service.request_approval(prepared["tool_call_id"])
    replay = await service.request_approval(prepared["tool_call_id"])

    assert request["status"] == "awaiting_approval"
    assert replay["status"] == "awaiting_approval"
    assert request["replayed"] is False
    assert replay["replayed"] is True
    assert request["proposal_payload"] == {"value": "input"}
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get(
            prepared["tool_call_id"]
        )
    assert row is not None
    assert row.status == "awaiting_approval"


async def test_request_approval_concurrent_calls_converge_on_durable_request(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    observation = _RepositoryObservation(approval_barrier=_TwoPartyBarrier())
    service = ToolCallService(
        session_factory=isolated_db.session,
        repositories=_ObservedRepositoryFactory(observation),
    ).bind_handlers({"demo": _DemoHandler()})
    prepared = await service.validate_call(
        _tool_context(conversation_id, run_id), _tool_call(value="input")
    )
    assert prepared["status"] == "validated"
    start = asyncio.Event()

    async def request() -> ToolCall:
        await start.wait()
        return await service.request_approval(prepared["tool_call_id"])

    first = asyncio.create_task(request())
    second = asyncio.create_task(request())
    start.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result["tool_call_id"] == second_result["tool_call_id"]
    assert first_result["status"] == second_result["status"] == "awaiting_approval"
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get(
            prepared["tool_call_id"]
        )
    assert row is not None and row.status == "awaiting_approval"


async def test_request_approval_replays_validation_time_result(isolated_db) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    service = _tool_service(isolated_db, _DemoHandler())
    completed = await service.validate_call(
        _tool_context(conversation_id, run_id), _tool_call(value="done")
    )
    assert completed["status"] == "resolved"

    replay = await service.request_approval(completed["tool_call_id"])

    assert replay["status"] == "resolved"
    assert replay["result"] == {"outcome": "no_change"}
    assert replay["replayed"] is True


async def test_record_decision_persists_approval_before_execution(isolated_db) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    service = _tool_service(isolated_db, _DemoHandler())
    prepared = await service.validate_call(
        _tool_context(conversation_id, run_id), _tool_call(value="input")
    )
    assert prepared["status"] == "validated"
    await service.request_approval(prepared["tool_call_id"])

    approved = await service.record_decision(
        {
            "tool_call_id": prepared["tool_call_id"],
            "decision": "approve",
            "client_resolution_id": "resolution-1",
        }
    )
    approval_replay = await service.record_decision(
        {
            "tool_call_id": prepared["tool_call_id"],
            "decision": "approve",
            "client_resolution_id": "resolution-1",
        }
    )
    request_replay = await service.request_approval(prepared["tool_call_id"])

    assert approved["status"] == "approved"
    assert approval_replay["status"] == "approved"
    assert request_replay["status"] == "approved"
    assert approved["replayed"] is False
    assert approval_replay["replayed"] is True
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get(
            prepared["tool_call_id"]
        )
    assert row is not None
    assert row.status == "approved"
    assert row.decision == "approve"
    assert row.client_resolution_id == "resolution-1"


async def test_record_decision_replays_resolved_approval_only_for_exact_identity(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    service = _tool_service(isolated_db, _ExecutionHandler())
    context = _tool_context(conversation_id, run_id)
    prepared = await service.validate_call(context, _tool_call(value="input"))
    assert prepared["status"] == "validated"
    await service.request_approval(prepared["tool_call_id"])
    approval = {
        "tool_call_id": prepared["tool_call_id"],
        "decision": "approve",
        "client_resolution_id": "resolved-approval",
    }
    await service.record_decision(approval)
    await service.execute_call(context, prepared["tool_call_id"])

    replay = await service.record_decision(approval)

    assert replay["status"] == "resolved"
    assert "decision" not in replay
    assert "client_resolution_id" not in replay
    assert replay["replayed"] is True
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get(
            prepared["tool_call_id"]
        )
    assert row is not None
    assert row.decision == "approve"
    assert row.client_resolution_id == "resolved-approval"
    for decision, token in (
        ("approve", "different-resolution"),
        ("reject", "resolved-approval"),
    ):
        with pytest.raises(IdempotencyConflictError):
            await service.record_decision(
                {
                    "tool_call_id": prepared["tool_call_id"],
                    "decision": decision,
                    "client_resolution_id": token,
                }
            )


async def test_record_decision_rejects_without_calling_handler_execute(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    handler = _DemoHandler()
    service = _tool_service(isolated_db, handler)
    context = _tool_context(conversation_id, run_id)
    prepared = await service.validate_call(
        context, _tool_call(value="input")
    )
    assert prepared["status"] == "validated"
    await service.request_approval(prepared["tool_call_id"])
    approval = {
        "tool_call_id": prepared["tool_call_id"],
        "decision": "reject",
        "client_resolution_id": "reject-1",
    }

    rejected = await service.record_decision(approval)
    replay = await service.record_decision(approval)

    assert rejected["status"] == "resolved"
    assert rejected["result"] == {"outcome": "rejected"}
    assert rejected["should_execute"] is False
    assert "decision" not in rejected
    assert "client_resolution_id" not in rejected
    assert rejected["replayed"] is False
    assert replay["status"] == "resolved"
    assert replay["result"] == rejected["result"]
    assert replay["replayed"] is True
    assert handler.execution_count == 0
    result = await service.execute_call(context, prepared["tool_call_id"])
    assert result.decision == "reject"
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get(
            prepared["tool_call_id"]
        )
    assert row is not None
    assert row.decision == "reject"
    assert row.client_resolution_id == "reject-1"


async def test_record_decision_rejects_empty_or_conflicting_resolution_identity(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    service = _tool_service(isolated_db, _DemoHandler())
    prepared = await service.validate_call(
        _tool_context(conversation_id, run_id), _tool_call(value="input")
    )
    assert prepared["status"] == "validated"
    await service.request_approval(prepared["tool_call_id"])

    for invalid_resolution_id in ("", "   "):
        with pytest.raises(ToolProtocolError, match="resolution"):
            await service.record_decision(
                {
                    "tool_call_id": prepared["tool_call_id"],
                    "decision": "approve",
                    "client_resolution_id": invalid_resolution_id,
                }
            )
    approved = await service.record_decision(
        {
            "tool_call_id": prepared["tool_call_id"],
            "decision": "approve",
            "client_resolution_id": "resolution-1",
        }
    )
    assert approved["status"] == "approved"

    for decision, token in (
        ("approve", "resolution-2"),
        ("reject", "resolution-1"),
    ):
        with pytest.raises(IdempotencyConflictError):
            await service.record_decision(
                {
                    "tool_call_id": prepared["tool_call_id"],
                    "decision": decision,
                    "client_resolution_id": token,
                }
            )


async def test_record_decision_concurrent_resolution_token_has_one_stable_owner(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    handler = _DemoHandler()
    service = _tool_service(isolated_db, handler)
    first = await service.validate_call(
        _tool_context(conversation_id, run_id), _tool_call(value="first", index=0)
    )
    second = await service.validate_call(
        _tool_context(conversation_id, run_id), _tool_call(value="second", index=1)
    )
    assert first["status"] == "validated"
    assert second["status"] == "validated"
    await service.request_approval(first["tool_call_id"])
    await service.request_approval(second["tool_call_id"])
    start = asyncio.Event()
    observation = _RepositoryObservation(
        resolution_barrier=_TwoPartyBarrier(),
        resolution_id="shared-resolution",
    )
    decision_service = ToolCallService(
        session_factory=isolated_db.session,
        repositories=_ObservedRepositoryFactory(observation),
    ).bind_handlers({"demo": handler})

    async def decide(tool_call_id: int):  # type: ignore[no-untyped-def]
        await start.wait()
        return await decision_service.record_decision(
            {
                "tool_call_id": tool_call_id,
                "decision": "approve",
                "client_resolution_id": "shared-resolution",
            }
        )

    first_task = asyncio.create_task(decide(first["tool_call_id"]))
    second_task = asyncio.create_task(decide(second["tool_call_id"]))
    start.set()
    outcomes = await asyncio.gather(first_task, second_task, return_exceptions=True)

    assert sum(
        isinstance(item, dict) and item.get("status") == "approved"
        for item in outcomes
    ) == 1
    assert sum(isinstance(item, IdempotencyConflictError) for item in outcomes) == 1
    assert not any(isinstance(item, IntegrityError) for item in outcomes)
    async with isolated_db.session() as session:
        repository = RepositoryFactory().create(session).tool_calls
        rows = [
            await repository.get(first["tool_call_id"]),
            await repository.get(second["tool_call_id"]),
        ]
    owners = [row for row in rows if row and row.client_resolution_id]
    assert len(owners) == 1
    assert owners[0].client_resolution_id == "shared-resolution"


async def test_execute_call_rolls_back_failure_then_retries_atomically(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    handler = _ExecutionHandler(fail_first=True)
    service = _tool_service(isolated_db, handler)
    context = _tool_context(conversation_id, run_id)
    prepared = await service.validate_call(context, _tool_call(value="input"))
    assert prepared["status"] == "validated"
    await service.request_approval(prepared["tool_call_id"])
    await service.record_decision(
        {
            "tool_call_id": prepared["tool_call_id"],
            "decision": "approve",
            "client_resolution_id": "approve-retry",
        }
    )

    with pytest.raises(RuntimeError, match="transient"):
        await service.execute_call(context, prepared["tool_call_id"])

    async with isolated_db.session() as session:
        repository = RepositoryFactory().create(session)
        failed_row = await repository.tool_calls.get(prepared["tool_call_id"])
        side_effect_count = await session.scalar(
            select(func.count()).select_from(AiChatMessage)
        )
    assert failed_row is not None and failed_row.status == "approved"
    assert side_effect_count == 0

    completed = await service.execute_call(context, prepared["tool_call_id"])

    assert completed.payload["outcome"] == "applied"
    assert completed.decision == "approve"
    assert completed.replayed is False
    assert handler.execution_count == 2
    assert handler.success_count == 1
    async with isolated_db.session() as session:
        side_effect_count = await session.scalar(
            select(func.count()).select_from(AiChatMessage)
        )
    assert side_effect_count == 1


async def test_execute_call_rejects_awaiting_approval(isolated_db) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    service = _tool_service(isolated_db, _DemoHandler())
    context = _tool_context(conversation_id, run_id)
    prepared = await service.validate_call(context, _tool_call(value="input"))
    assert prepared["status"] == "validated"
    await service.request_approval(prepared["tool_call_id"])

    with pytest.raises(ToolProtocolError, match="not ready"):
        await service.execute_call(context, prepared["tool_call_id"])


async def test_execute_call_rejects_context_for_another_persisted_run(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    other_conversation_id, other_run_id = await _create_conversation_run(isolated_db)
    handler = _DemoHandler()
    service = _tool_service(isolated_db, handler)
    prepared = await service.validate_call(
        _tool_context(conversation_id, run_id), _tool_call(value="input")
    )
    assert prepared["status"] == "validated"

    with pytest.raises(ToolProtocolError, match="identity"):
        await service.execute_call(
            _tool_context(other_conversation_id, other_run_id),
            prepared["tool_call_id"],
        )

    assert handler.execution_count == 0
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get(
            prepared["tool_call_id"]
        )
    assert row is not None and row.status == "validated"


async def test_execute_call_uses_only_persisted_payloads_and_replays_result(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    handler = _ExecutionHandler()
    service = _tool_service(isolated_db, handler)
    context = _tool_context(conversation_id, run_id)
    prepared = await service.validate_call(context, _tool_call(value="input"))
    assert prepared["status"] == "validated"
    async with isolated_db.session() as session:
        row = await RepositoryFactory().create(session).tool_calls.get(
            prepared["tool_call_id"]
        )
        assert row is not None
        row.proposal_payload = {"value": "persisted-proposal"}
        row.guard_payload = {"trusted": "persisted-guard"}
        await session.commit()

    completed = await service.execute_call(context, prepared["tool_call_id"])
    replay = await service.execute_call(context, prepared["tool_call_id"])

    assert completed.payload == {
        "outcome": "applied",
        "proposal": {"value": "persisted-proposal"},
        "guard": {"trusted": "persisted-guard"},
    }
    assert completed.decision is None
    assert completed.replayed is False
    assert replay.payload == completed.payload
    assert replay.replayed is True
    assert handler.received_payloads == [
        (
            {"value": "persisted-proposal"},
            {"trusted": "persisted-guard"},
        )
    ]
    assert handler.execution_count == 1


async def test_execute_call_concurrent_claim_runs_business_side_effect_once(
    isolated_db,
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    handler = _BlockingExecutionHandler()
    observation = _RepositoryObservation(
        second_execution_claim=asyncio.Event()
    )
    service = ToolCallService(
        session_factory=isolated_db.session,
        repositories=_ObservedRepositoryFactory(observation),
    ).bind_handlers({"demo": handler})
    context = _tool_context(conversation_id, run_id)
    prepared = await service.validate_call(context, _tool_call(value="input"))
    assert prepared["status"] == "validated"
    first = asyncio.create_task(
        service.execute_call(context, prepared["tool_call_id"])
    )
    await asyncio.wait_for(handler.entered.wait(), timeout=2)

    async def replay_while_executing() -> ToolResult:
        return await service.execute_call(context, prepared["tool_call_id"])

    second = asyncio.create_task(replay_while_executing())
    assert observation.second_execution_claim is not None
    await asyncio.wait_for(observation.second_execution_claim.wait(), timeout=2)
    handler.release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result.payload == {"outcome": "applied-once"}
    assert second_result.payload == first_result.payload
    assert sorted((first_result.replayed, second_result.replayed)) == [False, True]
    assert handler.execution_count == 1
    assert handler.success_count == 1
    async with isolated_db.session() as session:
        side_effect_count = await session.scalar(
            select(func.count()).select_from(AiChatMessage)
        )
    assert side_effect_count == 1


@pytest.mark.parametrize(
    ("first_arguments", "replayed_arguments"),
    [
        ({"value": True}, {"value": 1}),
        (
            {"nested": [{"value": False}]},
            {"nested": [{"value": 0}]},
        ),
    ],
)
async def test_materialize_rejects_json_values_with_different_types(
    isolated_db,
    first_arguments: dict[str, object],
    replayed_arguments: dict[str, object],
) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)
    async with isolated_db.session() as session:
        await RepositoryFactory().create(session).tool_calls.materialize(
            conversation_id=conversation_id,
            run_id=run_id,
            tool_call_index=0,
            provider_tool_call_id="provider-a",
            tool_name="demo",
            arguments=first_arguments,
        )
        await session.commit()

    async with isolated_db.session() as session:
        with pytest.raises(
            ToolProtocolError,
            match="index was reused inconsistently",
        ):
            await RepositoryFactory().create(session).tool_calls.materialize(
                conversation_id=conversation_id,
                run_id=run_id,
                tool_call_index=0,
                provider_tool_call_id="provider-b",
                tool_name="demo",
                arguments=replayed_arguments,
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
            encode_tool_call(
                index=0,
                provider_id=None,
                name="demo",
                arguments=json.dumps({"value": "input"}),
            ),
        )
        assert dispatched["status"] == "validated"
        assert dispatched["security"] == ToolSecurity.MEDIUM.value
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
