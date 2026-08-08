# ToolCallService Unified Tool Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Graph-owned Tool mechanics with one `ToolCallService` path that atomically materializes, validates, records approval, executes, and persists every current and future Tool Call.

**Architecture:** `ToolHandler` retains business validation/execution, `ToolCallRepository` retains SQL primitives, and the new application-layer `ToolCallService` owns Handler binding plus Tool Call transactions and state transitions. LangGraph keeps only routing, approval policy, `interrupt/resume`, and event emission; the database is the durable source of truth for calls, approval decisions, and results.

**Tech Stack:** Python 3.13, Pydantic 2, SQLAlchemy 2 async, SQLite/aiosqlite, LangGraph 1.2, pytest, Ruff.

## Global Constraints

- Follow `docs/superpowers/specs/2026-08-08-tool-call-service-design.zh-CN.md` exactly.
- Keep approval policy in Graph `guard`; `ToolCallService` may persist a decision but must not choose it.
- Production Graph code must not call `ToolHandler` or mutate `ToolCallRepository` directly.
- `proposal_payload` is user-facing; `guard_payload` is server-trusted and must be loaded from the database.
- Persist `approved` before entering executor; execute business writes and final Tool Result in one transaction.
- Keep LangGraph State JSON-only and preserve old checkpoint recovery.
- Do not add dependencies or a second Tool table.
- Preserve unrelated dirty frontend files and never stage them.
- Use `apply_patch` for edits and UTF-8 for Chinese files.

---

## File Responsibility Map

- `app/ai_chat/services/tool_call_service.py`: bound Handler registry and all production Tool Call coordination.
- `app/ai_chat/tools/results.py`: immutable, ORM-free dispatch result types shared by Service and Graph.
- `app/ai_chat/repositories/tool_call_repository.py`: atomic materialization and state-transition CAS primitives.
- `app/ai_chat/models/models.py`: explicit Tool Call state constraints.
- `app/scripts/migrate_ai_chat_tool_call_state.py`: old-row backfill and SQLite table rebuild.
- `app/ai_chat/graph/runtime.py`: model plus bound `ToolCallService`; no Repository exposure.
- `app/experience/graph/builder.py`: Graph topology, policy, interrupt/resume, and events only.
- `app/experience/tools/content_change.py`: business Handler using the Service-injected session.
- `tests/unit/test_tool_call_service.py`: focused state, repository, and Service contract tests.
- `tests/unit/test_experience_ai_chat.py`: real Experience Graph and recovery integration tests.

---

### Task 1: Add the explicit persistent Tool Call state machine

**Files:**
- Create: `apps/backend/app/scripts/migrate_ai_chat_tool_call_state.py`
- Modify: `apps/backend/app/ai_chat/models/models.py:125-185`
- Modify: `apps/backend/app/ai_chat/repositories/tool_call_repository.py`
- Modify: `apps/backend/app/db_engine.py:62-96`
- Create: `apps/backend/tests/unit/test_tool_call_service.py`

**Interfaces:**
- Consumes: existing `AiChatToolCall` columns and `schema_migrations` convention.
- Produces: statuses `received`, `validated`, `awaiting_approval`, `approved`, `executing`, `resolved`; migration name `2026_08_08_ai_chat_tool_call_state`.

- [ ] **Step 1: Write the legacy-state migration test**

Create a raw old-schema database with four rows and assert the exact backfill:

```python
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
    finally:
        engine.dispose()
```

- [ ] **Step 2: Run the migration test and verify it fails**

Run:

```powershell
$env:DATA_DIR = 'C:\Users\jvxi\AppData\Local\Temp\resume-matcher-tool-service'
& 'E:\anaconda\envs\resume-matcher\python.exe' -m pytest tests/unit/test_tool_call_service.py::test_tool_call_state_migration_backfills_validated -q
```

Expected: collection/import fails because `migrate_ai_chat_tool_call_state` does not exist.

- [ ] **Step 3: Expand the ORM constraints**

Replace the Tool status check with:

```python
CheckConstraint(
    "status IN ('received', 'validated', 'awaiting_approval', "
    "'approved', 'executing', 'resolved')",
    name="ck_ai_chat_tool_status",
),
CheckConstraint(
    "status != 'resolved' OR tool_result IS NOT NULL",
    name="ck_ai_chat_tool_result",
),
```

- [ ] **Step 4: Implement the idempotent SQLite migration**

In `migrate_ai_chat_tool_call_state.py`:

```python
MIGRATION_NAME = "2026_08_08_ai_chat_tool_call_state"

def migrate(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name VARCHAR(200) PRIMARY KEY, applied_at VARCHAR NOT NULL)"
        )
        if connection.scalar(
            text("SELECT 1 FROM schema_migrations WHERE name = :name"),
            {"name": MIGRATION_NAME},
        ):
            return
        table_sql = connection.scalar(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='ai_chat_tool_calls'"
            )
        ) or ""
        if "'validated'" not in table_sql:
            connection.exec_driver_sql(
                "CREATE TABLE ai_chat_tool_calls_state_v2 ("
                "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,"
                "conversation_id INTEGER NOT NULL REFERENCES "
                "ai_chat_conversations(id) ON DELETE CASCADE,"
                "run_id INTEGER NOT NULL REFERENCES ai_chat_runs(id) ON DELETE CASCADE,"
                "tool_call_index INTEGER NOT NULL,"
                "provider_tool_call_id VARCHAR(200),"
                "tool_name VARCHAR(160) NOT NULL,"
                "arguments JSON NOT NULL,proposal_payload JSON,guard_payload JSON,"
                "status VARCHAR(24) NOT NULL,decision VARCHAR(16),tool_result JSON,"
                "delivery_status VARCHAR(16),client_resolution_id VARCHAR(160),"
                "resolved_at VARCHAR,created_at VARCHAR NOT NULL,updated_at VARCHAR NOT NULL,"
                "CONSTRAINT ck_ai_chat_tool_status CHECK (status IN "
                "('received','validated','awaiting_approval','approved','executing','resolved')),"
                "CONSTRAINT ck_ai_chat_tool_decision CHECK "
                "(decision IS NULL OR decision IN ('approve','reject')),"
                "CONSTRAINT ck_ai_chat_tool_delivery CHECK "
                "(delivery_status IS NULL OR delivery_status IN ('pending','consumed')),"
                "CONSTRAINT ck_ai_chat_tool_result CHECK "
                "(status != 'resolved' OR tool_result IS NOT NULL),"
                "CONSTRAINT uq_ai_chat_tool_resolution_id UNIQUE "
                "(conversation_id,client_resolution_id))"
            )
            connection.exec_driver_sql(
                "INSERT INTO ai_chat_tool_calls_state_v2 "
                "(id,conversation_id,run_id,tool_call_index,provider_tool_call_id,"
                "tool_name,arguments,proposal_payload,guard_payload,status,decision,"
                "tool_result,delivery_status,client_resolution_id,resolved_at,"
                "created_at,updated_at) SELECT id,conversation_id,run_id,"
                "tool_call_index,provider_tool_call_id,tool_name,arguments,"
                "proposal_payload,guard_payload,CASE WHEN status='received' "
                "AND proposal_payload IS NOT NULL AND guard_payload IS NOT NULL "
                "THEN 'validated' ELSE status END,decision,tool_result,delivery_status,"
                "client_resolution_id,resolved_at,created_at,updated_at "
                "FROM ai_chat_tool_calls"
            )
            connection.exec_driver_sql("DROP TABLE ai_chat_tool_calls")
            connection.exec_driver_sql(
                "ALTER TABLE ai_chat_tool_calls_state_v2 RENAME TO ai_chat_tool_calls"
            )
            for sql in (
                "CREATE INDEX ix_ai_chat_tool_calls_conversation_id "
                "ON ai_chat_tool_calls (conversation_id)",
                "CREATE INDEX ix_ai_chat_tool_calls_run_id ON ai_chat_tool_calls (run_id)",
                "CREATE INDEX ix_ai_chat_tool_calls_status ON ai_chat_tool_calls (status)",
                "CREATE INDEX ix_ai_chat_tool_calls_delivery_status "
                "ON ai_chat_tool_calls (delivery_status)",
                "CREATE UNIQUE INDEX ux_ai_chat_tool_run_index "
                "ON ai_chat_tool_calls (run_id,tool_call_index)",
                "CREATE UNIQUE INDEX ux_ai_chat_tool_provider_call "
                "ON ai_chat_tool_calls (run_id,provider_tool_call_id) "
                "WHERE provider_tool_call_id IS NOT NULL",
            ):
                connection.exec_driver_sql(sql)
        connection.execute(
            text(
                "INSERT INTO schema_migrations (name,applied_at) "
                "VALUES (:name,:now)"
            ),
            {"name": MIGRATION_NAME, "now": utcnow_iso()},
        )
```

- [ ] **Step 5: Keep the existing low-risk path constraint-safe**

Before the full Repository transition rewrite in Task 2, change the existing `claim_execution()` target from `status="resolved"` to `status="executing"`. Keep its current `received` source status for compatibility. The surrounding execution transaction will still call `resolve()` with a non-null result, while rollback restores `received`.

- [ ] **Step 6: Register the migration after existing Tool index migration**

In `init_models_sync()` import and call:

```python
from app.scripts.migrate_ai_chat_tool_call_state import (
    migrate as migrate_ai_chat_tool_call_state,
)

migrate_ai_chat_tool_call_index(engine)
migrate_ai_chat_tool_call_state(engine)
```

- [ ] **Step 7: Run the migration, database, and low-risk compatibility tests**

Run:

```powershell
& 'E:\anaconda\envs\resume-matcher\python.exe' -m pytest tests/unit/test_tool_call_service.py::test_tool_call_state_migration_backfills_validated tests/unit/test_database.py tests/unit/test_experience_ai_chat.py -k "tool_call_state_migration or database or low_risk" -q
```

Expected: all selected tests pass and running the migration twice is a no-op.

- [ ] **Step 8: Commit the state schema**

```powershell
git add apps/backend/app/ai_chat/models/models.py apps/backend/app/ai_chat/repositories/tool_call_repository.py apps/backend/app/db_engine.py apps/backend/app/scripts/migrate_ai_chat_tool_call_state.py apps/backend/tests/unit/test_tool_call_service.py
git commit -m "feat: add durable tool call states"
```

---

### Task 2: Make Repository materialization and transitions atomic

**Files:**
- Modify: `apps/backend/app/ai_chat/repositories/tool_call_repository.py`
- Modify: `apps/backend/tests/unit/test_tool_call_service.py`

**Interfaces:**
- Consumes: statuses from Task 1.
- Produces:
  - `materialize(*, conversation_id: int, run_id: int, tool_call_index: int, provider_tool_call_id: str | None, tool_name: str, arguments: JsonObject) -> AiChatToolCall`
  - `claim_approval_request(tool_call_id: int) -> bool`
  - `approve(tool_call_id: int, client_resolution_id: str) -> bool`
  - `claim_rejection(tool_call_id: int, client_resolution_id: str) -> bool`
  - `claim_execution(tool_call_id: int, from_status: Literal["validated", "approved"]) -> bool`

- [ ] **Step 1: Add failing concurrent materialization tests**

Add a helper that creates one Conversation and Run, then run two independent sessions:

```python
async def test_materialize_is_atomic_under_concurrent_replay(isolated_db) -> None:
    conversation_id, run_id = await _create_conversation_run(isolated_db)

    async def worker(provider_id: str) -> int:
        async with isolated_db.session() as session:
            repository = RepositoryFactory().create(session).tool_calls
            row = await repository.materialize(
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
```

Also call `materialize()` again with `arguments={"value": "different"}` and assert `ToolProtocolError`.

- [ ] **Step 2: Run the tests and verify the concurrent case fails**

Run:

```powershell
& 'E:\anaconda\envs\resume-matcher\python.exe' -m pytest tests/unit/test_tool_call_service.py -k "materialize" -q
```

Expected: the pre-existing `SELECT → INSERT` implementation can raise a unique-key error under concurrency.

- [ ] **Step 3: Replace SELECT-then-INSERT with SQLite upsert/reload**

Use:

```python
statement = (
    sqlite_insert(AiChatToolCall)
    .values(
        conversation_id=conversation_id,
        run_id=run_id,
        tool_call_index=tool_call_index,
        provider_tool_call_id=provider_tool_call_id,
        tool_name=tool_name,
        arguments=arguments,
    )
    .on_conflict_do_nothing()
)
await self._session.execute(statement)
row = await self.get_by_run_index(run_id, tool_call_index)
if row is None or row.tool_name != tool_name or row.arguments != arguments:
    raise ToolProtocolError("Tool Call index was reused inconsistently")
return row
```

This preserves the first provider ID and converts provider/index identity conflicts into a stable protocol error.

- [ ] **Step 4: Add state-transition tests**

Test these exact transitions and failed CAS cases:

```text
received --save_validation--> validated
validated --claim_approval_request--> awaiting_approval
awaiting_approval --approve--> approved
awaiting_approval --claim_rejection--> executing --resolve--> resolved
validated/approved --claim_execution--> executing --resolve--> resolved
```

Assert `resolve()` sets `delivery_status="pending"` and refuses a missing result through the database constraint.

- [ ] **Step 5: Implement the Repository CAS primitives**

Each claim must use one SQLAlchemy `update(AiChatToolCall)` whose `where()` includes the exact source status, then return `rowcount == 1`. Change `save_validation()` to set `status="validated"`; change `resolve()` to set `status="resolved"` only when writing `tool_result` in the same flush.

- [ ] **Step 6: Run Repository tests**

Run:

```powershell
& 'E:\anaconda\envs\resume-matcher\python.exe' -m pytest tests/unit/test_tool_call_service.py -k "materialize or repository_transition" -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit Repository invariants**

```powershell
git add apps/backend/app/ai_chat/repositories/tool_call_repository.py apps/backend/tests/unit/test_tool_call_service.py
git commit -m "refactor: make tool call transitions atomic"
```

---

### Task 3: Implement Handler binding and validation in ToolCallService

**Files:**
- Create: `apps/backend/app/ai_chat/services/tool_call_service.py`
- Modify: `apps/backend/app/ai_chat/services/__init__.py`
- Modify: `apps/backend/app/ai_chat/tools/handler.py`
- Create: `apps/backend/app/ai_chat/tools/security.py`
- Modify: `apps/backend/app/ai_chat/tools/results.py`
- Modify: `apps/backend/tests/unit/test_tool_call_service.py`

**Interfaces:**
- Consumes: Task 2 Repository API, `ToolHandler`, `ToolContext`, `AssembledToolCall`.
- Produces:
  - `ToolCallService.bind_handlers()` and `model_handlers`
  - `validate_call(context, call) -> ToolCallState`
  - immutable `PreparedToolCall`, `ApprovalRequest`, `ApprovedToolCall`, `CompletedToolCall`.

- [ ] **Step 1: Define failing Service validation tests**

Create a strict fake Handler:

```python
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

    async def validation(self, context, arguments):
        self.validation_count += 1
        values = self.arguments_schema.model_validate(arguments)
        if values.value == "done":
            return self.show_result({"outcome": "no_change"})
        return ValidatedToolCall(
            proposal_payload={"value": values.value},
            guard_payload={"trusted": values.value},
        )

    async def execute(self, context, proposal_payload, guard_payload):
        self.execution_count += 1
        return self.show_result({"outcome": "applied"})

    def show_result(self, payload):
        return ToolResult(dict(payload))
```

Tests must assert:

- `bind_handlers()` exposes the exact bound mapping without mutating the base Service;
- unknown tool raises `ToolProtocolError` before a row is created;
- invalid arguments leave one durable `received` row;
- prepared validation returns `PreparedToolCall` and stores trusted payloads as `validated`;
- immediate result returns `CompletedToolCall` and stores `resolved`;
- replay does not call Handler validation twice;
- existing `awaiting_approval`, `approved`, and `resolved` rows map to the matching immutable return type.

- [ ] **Step 2: Run Service validation tests and verify they fail**

Run:

```powershell
& 'E:\anaconda\envs\resume-matcher\python.exe' -m pytest tests/unit/test_tool_call_service.py -k "bind_handlers or validate_call" -q
```

Expected: import fails because `ToolCallService` and dispatch types do not exist.

- [ ] **Step 3: Add immutable dispatch types**

In `tools/results.py` define:

```python
@dataclass(frozen=True)
class PreparedToolCall:
    tool_call_id: int
    tool_name: str
    security: ToolSecurity

@dataclass(frozen=True)
class ApprovalRequest:
    tool_call_id: int
    tool_name: str
    proposal_payload: JsonObject

@dataclass(frozen=True)
class ApprovedToolCall:
    tool_call_id: int
    tool_name: str
    client_resolution_id: str

@dataclass(frozen=True)
class CompletedToolCall:
    tool_call_id: int
    tool_name: str
    result: JsonObject
    decision: Literal["approve", "reject"] | None
    replayed: bool

ToolCallState = (
    PreparedToolCall | ApprovalRequest | ApprovedToolCall | CompletedToolCall
)
```

- [ ] **Step 4: Implement the bound Service shell**

Use an immutable dataclass and injected session factory:

```python
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

@dataclass(frozen=True)
class ToolCallService:
    session_factory: SessionFactory
    repositories: RepositoryFactory
    handlers: Mapping[str, ToolHandler] = field(default_factory=dict)

    def bind_handlers(self, handlers):
        return replace(self, handlers=MappingProxyType(dict(handlers)))

    @property
    def model_handlers(self):
        return self.handlers
```

Add a private `_handler(name)` that raises `ToolProtocolError(f"Unknown tool: {name}")`.

- [ ] **Step 5: Implement validate_call with two durable boundaries**

The method must:

1. Resolve Handler.
2. Open session A, `materialize()`, commit, retain ID.
3. Open session B and reload the row.
4. Return an immutable snapshot for `validated`, `awaiting_approval`, `approved`, or `resolved` without revalidating.
5. For `received`, call `handler.validation(replace(context, tool_call_id=id, session=session), row.arguments)`.
6. Save `ValidatedToolCall` as `validated`, or save immediate `ToolResult` as `resolved`.
7. Commit before returning.

Any unsupported status or result type raises `ToolProtocolError`; exceptions roll back session B while the raw `received` row remains durable.

- [ ] **Step 6: Export Service and result types**

Export `ToolCallService` from `ai_chat/services/__init__.py` and the new immutable results from `ai_chat/tools/__init__.py` if that module is the public Tool protocol surface.

- [ ] **Step 7: Run validation tests**

Run:

```powershell
& 'E:\anaconda\envs\resume-matcher\python.exe' -m pytest tests/unit/test_tool_call_service.py -k "bind_handlers or validate_call" -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit the validation service**

```powershell
git add apps/backend/app/ai_chat/services/tool_call_service.py apps/backend/app/ai_chat/services/__init__.py apps/backend/app/ai_chat/tools/handler.py apps/backend/app/ai_chat/tools/security.py apps/backend/app/ai_chat/tools/results.py apps/backend/app/ai_chat/tools/__init__.py apps/backend/tests/unit/test_tool_call_service.py
git commit -m "feat: add tool call validation service"
```

---

### Task 4: Persist approval intent and execute through ToolCallService

**Files:**
- Modify: `apps/backend/app/ai_chat/services/tool_call_service.py`
- Modify: `apps/backend/tests/unit/test_tool_call_service.py`

**Interfaces:**
- Consumes: Task 3 Service and immutable dispatch types.
- Produces:
  - `request_approval(tool_call_id)`
  - `record_decision(approval)`
  - `execute_call(context, tool_call_id)`.

- [ ] **Step 1: Add failing approval and execution tests**

Cover these observable behaviors:

```python
request = await service.request_approval(prepared.tool_call_id)
assert isinstance(request, ApprovalRequest)

approved = await service.record_decision({
    "tool_call_id": prepared.tool_call_id,
    "decision": "approve",
    "client_resolution_id": "resolution-1",
})
assert isinstance(approved, ApprovedToolCall)

async with isolated_db.session() as session:
    row = await RepositoryFactory().create(session).tool_calls.get(
        prepared.tool_call_id
    )
assert row is not None and row.status == "approved"
```

Also assert:

- repeated `request_approval()` is idempotent;
- reject stores `CompletedToolCall(result={"outcome": "rejected"})` and never calls Handler `execute`;
- same resolution token replays; different token or decision raises `IdempotencyConflictError`;
- approve is durable before execute;
- a fail-once Handler leaves status `approved`, then succeeds on retry exactly once;
- low-risk validated calls execute without approval;
- resolved calls only replay stored results;
- execution uses persisted proposal/guard values, never caller-supplied copies.

- [ ] **Step 2: Run approval tests and verify they fail**

Run:

```powershell
& 'E:\anaconda\envs\resume-matcher\python.exe' -m pytest tests/unit/test_tool_call_service.py -k "approval or decision or execute_call" -q
```

Expected: methods are missing.

- [ ] **Step 3: Implement request_approval**

Load the row and return by durable state:

```text
validated -> CAS awaiting_approval -> ApprovalRequest
awaiting_approval -> ApprovalRequest(replayed)
approved -> ApprovedToolCall
resolved -> CompletedToolCall(replayed=True)
other -> ToolProtocolError
```

Commit the transition before returning the user-facing proposal.

- [ ] **Step 4: Implement record_decision**

Before claiming, query `get_by_resolution_id()` and reject a token owned by another call.

For approve:

```text
CAS awaiting_approval -> approved
write decision=approve + client_resolution_id
commit
```

For reject:

```text
CAS awaiting_approval -> executing
write decision=reject + client_resolution_id
resolve with {"outcome":"rejected"}
commit
```

When already approved/resolved, compare both decision and resolution ID; return the stored state only on an exact match.

- [ ] **Step 5: Implement execute_call**

The method must load `tool_name`, `proposal_payload`, and `guard_payload` from the row, then:

```text
validated or approved
→ CAS to executing
→ handler.execute(context with same session, persisted proposal, persisted guard)
→ verify ToolResult
→ repository.resolve(row, decision=row.decision, tool_result=result, client_resolution_id=row.client_resolution_id)
→ one commit
```

If Handler raises, the transaction rolls back to `validated` or `approved`. If CAS loses a race, refresh and replay only a fully resolved row; otherwise raise a stable state error.

- [ ] **Step 6: Run the full Service test file**

Run:

```powershell
& 'E:\anaconda\envs\resume-matcher\python.exe' -m pytest tests/unit/test_tool_call_service.py -q
```

Expected: all Service, Repository, migration, and concurrency tests pass.

- [ ] **Step 7: Commit approval and execution**

```powershell
git add apps/backend/app/ai_chat/services/tool_call_service.py apps/backend/tests/unit/test_tool_call_service.py
git commit -m "feat: centralize tool approval and execution"
```

---

### Task 5: Bind ToolCallService into Runtime and business Handlers

**Files:**
- Modify: `apps/backend/app/ai_chat/graph/runtime.py`
- Modify: `apps/backend/app/ai_chat/graph/runner.py`
- Modify: `apps/backend/app/ai_chat/graph/state.py`
- Modify: `apps/backend/app/ai_chat/container.py`
- Modify: `apps/backend/app/experience/adapters/adapter.py`
- Modify: `apps/backend/app/experience/tools/content_change.py`
- Modify: `apps/backend/tests/unit/test_ai_chat_model.py`
- Modify: `apps/backend/tests/unit/test_experience_ai_chat.py`

**Interfaces:**
- Consumes: complete `ToolCallService` from Task 4.
- Produces: `AiChatRuntime(model, tools)` where `tools` is the only Tool dependency exposed to Graph.

- [ ] **Step 1: Update Runtime construction tests first**

Replace test Runtime creation with:

```python
tools = ToolCallService(isolated_db.session, RepositoryFactory())
runtime = AiChatRuntime(model, tools).bind_tools(adapter.get_tool_handlers())
assert tuple(runtime.tools.model_handlers) == ("content_change",)
```

Add an assertion that the unbound Runtime remains empty after `bind_tools()`.

- [ ] **Step 2: Run Runtime/model tests and verify they fail**

Run:

```powershell
& 'E:\anaconda\envs\resume-matcher\python.exe' -m pytest tests/unit/test_ai_chat_model.py tests/unit/test_experience_ai_chat.py -k "runtime or exposes_provider_tools" -q
```

Expected: `AiChatRuntime` still expects `RepositoryFactory` and exposes `tool_handlers`.

- [ ] **Step 3: Replace Runtime fields**

Implement:

```python
@dataclass(frozen=True)
class AiChatRuntime:
    model: AiChatModel
    tools: ToolCallService

    def bind_tools(self, handlers):
        return AiChatRuntime(self.model, self.tools.bind_handlers(handlers))

    async def stream_model(
        self,
        *,
        messages: list[JsonObject],
        tools_enabled: bool,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ModelStreamEvent]:
        async for event in self.model.stream(
            messages=messages,
            handlers=self.tools.model_handlers,
            tools_enabled=tools_enabled,
            max_tokens=max_tokens,
        ):
            yield event
```

`GraphRunner._compiled()` may keep calling `runtime.bind_tools(adapter.get_tool_handlers())`; binding now delegates to Service.

- [ ] **Step 4: Assemble the Service in container**

In `start_ai_chat()`:

```python
tools = ToolCallService(database_module.db.session, _repositories)
runtime = AiChatRuntime(AiChatModel(), tools)
```

Keep `AiChatService` using `_repositories` for Conversation/Run and result-delivery reads.

- [ ] **Step 5: Require the injected validation session in ContentChangeHandler**

Replace the internal global session with:

```python
session = context.session
if session is None:
    raise RuntimeError("tool validation requires a shared transaction")
service = ExperienceAiMutationService(session)
```

Keep execute using the same injected session and keep `execute()` calling `show_result()` internally.

- [ ] **Step 6: Update direct Handler tests**

Wrap `handler.validation()` in `async with isolated_db.session()` and use `replace(context, session=session)`. Do not make production Handler tests bypass the shared-session requirement.

- [ ] **Step 7: Run Runtime and Handler tests**

Run:

```powershell
& 'E:\anaconda\envs\resume-matcher\python.exe' -m pytest tests/unit/test_ai_chat_model.py tests/unit/test_experience_ai_chat.py -k "handler or runtime or provider_tools or content_change" -q
```

Expected: selected tests pass.

- [ ] **Step 8: Commit Runtime integration**

```powershell
git add apps/backend/app/ai_chat/graph/runtime.py apps/backend/app/ai_chat/graph/runner.py apps/backend/app/ai_chat/graph/state.py apps/backend/app/ai_chat/container.py apps/backend/app/experience/adapters/adapter.py apps/backend/app/experience/tools/content_change.py apps/backend/tests/unit/test_ai_chat_model.py apps/backend/tests/unit/test_experience_ai_chat.py
git commit -m "refactor: bind tool service into runtime"
```

---

### Task 6: Reduce Experience Graph to orchestration only

**Files:**
- Modify: `apps/backend/app/experience/graph/state.py`
- Modify: `apps/backend/app/experience/graph/builder.py`
- Modify: `apps/backend/app/ai_chat/graph/runner.py`
- Modify: `apps/backend/app/ai_chat/services/service.py`
- Modify: `apps/backend/tests/unit/test_experience_ai_chat.py`

**Interfaces:**
- Consumes: bound `runtime.tools` and dispatch types.
- Produces: unchanged node names `llm`, `validator`, `guard`, `approver`, `executor`, with optional JSON `tool_phase`.

- [ ] **Step 1: Tighten Graph behavior tests before refactoring**

Ensure tests assert all of the following:

```text
llm -> validator -> guard
validated LOW -> executor -> END
validated MEDIUM -> approver interrupt
approve -> durable approved -> executor -> proposal.resolved -> tool event
reject -> proposal.resolved -> rejected event; execute count remains zero
executor failure -> run.failed; DB remains approved
same resolution retry -> one business mutation
different decision/token after failure -> IdempotencyConflictError
legacy checkpoint approval + DB awaiting -> persist decision then execute
```

Add an architecture assertion using source inspection or a focused grep test that `experience/graph/builder.py` contains neither `runtime.repositories` nor `handler.validation` nor `handler.execute`.

- [ ] **Step 2: Run real Graph tests and record the failing baseline**

Run:

```powershell
& 'E:\anaconda\envs\resume-matcher\python.exe' -m pytest tests/unit/test_experience_ai_chat.py -k "graph or approval or executor or recovery" -q
```

Expected: new durable-approved and architecture assertions fail.

- [ ] **Step 3: Add JSON tool_phase to ExperienceState**

Define:

```python
tool_phase: Literal[
    "validated", "awaiting_approval", "approved", "resolved"
] | None
```

Initialize it to `None` in input/LLM reset paths. Do not store Service result objects in State.

- [ ] **Step 4: Replace validator internals**

The node constructs `ToolContext`, calls only:

```python
dispatch = await runtime.tools.validate_call(context, call)
```

Map dispatch to JSON state. Emit Tool events only for `CompletedToolCall`, after Service has committed.

- [ ] **Step 5: Replace guard internals**

Route persisted phases first:

```text
awaiting_approval -> request_approval() -> approver
approved -> executor
resolved -> END
validated -> guard_tool(dispatch security)
```

For a new approval, call `runtime.tools.request_approval()`, emit `proposal.requested`, then set `proposal_id` and `tool_phase="awaiting_approval"`.

- [ ] **Step 6: Replace approver internals**

After `interrupt()` returns, call:

```python
dispatch = await runtime.tools.record_decision(approval)
```

For reject, emit `proposal.resolved` and rejected Tool event, then end. For approve, set `tool_phase="approved"` and route executor. The database commit must occur before the node returns and before LangGraph writes the next checkpoint.

- [ ] **Step 7: Replace executor internals**

For an old checkpoint with `state.approval` and DB still awaiting, call `record_decision()` idempotently first. Then call only:

```python
completed = await runtime.tools.execute_call(context, tool_call_id)
```

Emit `proposal.resolved` only for approved calls, then emit the Tool business event. Remove `_handler()`, all direct Repository calls, approval CAS, and Handler calls from builder.

- [ ] **Step 8: Keep recovery comparison DB-compatible**

Retain `GraphRunner.ensure_interrupted()` comparison of checkpoint approval to the incoming approval. In `AiChatService.resolve_proposal()`, treat durable `approved` with the same decision/token as resumable, and reject conflicting decisions before auto-progressing executor.

- [ ] **Step 9: Run Experience Graph tests**

Run:

```powershell
& 'E:\anaconda\envs\resume-matcher\python.exe' -m pytest tests/unit/test_experience_ai_chat.py -q
```

Expected: all Experience AI Chat tests pass, including real interrupt/resume and failure recovery.

- [ ] **Step 10: Commit Graph migration**

```powershell
git add apps/backend/app/experience/graph/state.py apps/backend/app/experience/graph/builder.py apps/backend/app/ai_chat/graph/runner.py apps/backend/app/ai_chat/services/service.py apps/backend/tests/unit/test_experience_ai_chat.py
git commit -m "refactor: route graph tools through tool service"
```

---

### Task 7: Remove old design references and verify the complete backend

**Files:**
- Delete: `apps/backend/app/ai_chat/tools/lifecycle.py` if still present
- Modify: `docs/superpowers/specs/2026-08-01-ai-chat-functional-boundaries-design.zh-CN.md`
- Modify: `docs/superpowers/specs/2026-08-01-experience-adapter-design.zh-CN.md`
- Modify: `docs/superpowers/specs/2026-08-08-ai-chat-memory-and-context-design.zh-CN.md`
- Modify: `docs/agent/learning/agent-development-reading-path.zh-CN.md`
- Modify: any backend exports/imports found by the cleanup search

**Interfaces:**
- Consumes: completed implementation from Tasks 1-6.
- Produces: one documented Tool path with no production reference to old Lifecycle or direct Graph execution.

- [ ] **Step 1: Search for stale code and documentation**

Run:

```powershell
rg -n "ToolLifecycle|tools/lifecycle.py|handler\.invoke|handler\.resolve|llm.*tool_executor|tool_executor.*approver|runtime\.repositories|handler\.validation|handler\.execute" apps/backend/app docs/superpowers/specs/2026-08-01-ai-chat-functional-boundaries-design.zh-CN.md docs/superpowers/specs/2026-08-01-experience-adapter-design.zh-CN.md docs/superpowers/specs/2026-08-08-ai-chat-memory-and-context-design.zh-CN.md docs/agent/learning/agent-development-reading-path.zh-CN.md
```

Expected before cleanup: only stale documents and any missed production bypasses are listed.

- [ ] **Step 2: Rewrite old descriptions to the implemented route**

Every updated document must describe:

```text
llm -> validator -> guard -> approver/executor -> END
Handler business methods
ToolCallService application coordination
Repository SQL/CAS
approved persisted before executor
```

Do not rewrite unrelated historical sections. Mark superseded interfaces as replaced rather than leaving two valid-looking designs.

- [ ] **Step 3: Verify stale production paths are gone**

Run the Step 1 command again.

Expected: no production-code match; documentation matches may only be explicit statements that the old design was removed.

- [ ] **Step 4: Run Ruff on all changed backend code**

Run:

```powershell
& 'E:\anaconda\Scripts\ruff.exe' check app tests
```

Expected: `All checks passed!`

- [ ] **Step 5: Verify fresh-process imports and Graph construction**

Run:

```powershell
& 'E:\anaconda\envs\resume-matcher\python.exe' -c "from app.ai_chat.services import ToolCallService; from app.ai_chat.graph.runtime import AiChatRuntime; from app.experience.graph import build_experience_graph; print('imports ok')"
```

Expected: `imports ok` with no circular import or annotation error.

- [ ] **Step 6: Run the full backend suite in an isolated data directory**

Run:

```powershell
$env:DATA_DIR = 'C:\Users\jvxi\AppData\Local\Temp\resume-matcher-tool-service'
& 'E:\anaconda\envs\resume-matcher\python.exe' -m pytest -q
```

Expected: all non-eval backend tests pass; no test writes to the developer database.

- [ ] **Step 7: Check the complete diff without touching frontend work**

Run:

```powershell
git diff --check
git status --short
```

Confirm the unrelated frontend files remain unchanged by this implementation and are not staged.

- [ ] **Step 8: Commit cleanup and documentation**

```powershell
git add apps/backend/app/ai_chat apps/backend/app/experience/adapters/adapter.py apps/backend/app/experience/graph apps/backend/app/experience/tools/content_change.py apps/backend/app/scripts apps/backend/tests docs/superpowers/specs/2026-08-01-ai-chat-functional-boundaries-design.zh-CN.md docs/superpowers/specs/2026-08-01-experience-adapter-design.zh-CN.md docs/superpowers/specs/2026-08-08-ai-chat-memory-and-context-design.zh-CN.md docs/agent/learning/agent-development-reading-path.zh-CN.md
git commit -m "docs: remove legacy tool lifecycle design"
```

Do not stage `apps/frontend` or any file outside the ToolCallService scope.
