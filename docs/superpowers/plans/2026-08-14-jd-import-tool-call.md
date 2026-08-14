# JD Import Tool Call Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace JD Import's `AiChatRun.result_json` bookkeeping with durable `ask_jd_questions` and `persist_jd` Tool Calls.

**Architecture:** Extend the shared Tool Call state machine with typed external input, while keeping approval semantics unchanged. The `plan_questions` node alone exposes the model-visible question tool; persistence calls are created by the Graph with stable business identities and execute automatically in the Tool Call transaction.

**Tech Stack:** Python 3.13, Pydantic 2, SQLAlchemy asyncio, SQLite, LangGraph 1.2, LiteLLM, pytest.

## Global Constraints

- `ask_jd_questions` is model-visible only inside `plan_questions`; the model decides whether to call it.
- Question Tool Results are delivered to the extraction model exactly once and also converted to grounded `user_answer` sources.
- `persist_jd` is system-only, `LOW` risk, and never delivered to the model.
- Stable identities are `jd-import:questions:{round}` and `jd-import:persist:{jd_key}` within one Run.
- Keep at most 12 questions per batch, 3 question rounds, and never ask one stable question key twice.
- Missing required facts or unresolved required conflicts persist as `incomplete`; incomplete JDs still persist.
- Preserve the public JD Agent API and all existing Experience approval behavior.
- Remove `AiChatRun.result_json` only after both JD use cases have moved to Tool Calls.

---

### Task 1: Add Tool visibility and result-delivery capabilities

**Files:**
- Modify: `apps/backend/app/ai_chat/tools/handler.py`
- Modify: `apps/backend/app/ai_chat/streaming/model.py`
- Modify: `apps/backend/app/ai_chat/services/ai_chat_service.py`
- Modify: `apps/backend/tests/unit/test_tool_call_service.py`
- Modify: `apps/backend/tests/unit/test_experience_ai_chat.py`

**Interfaces:**
- Produces `ToolHandler.model_visible: bool = True`.
- Produces `ToolHandler.deliver_result_to_model: bool = True`.
- `build_model_tools()` emits only handlers whose `model_visible` is true.
- `_build_input()` includes pending results only when the bound handler allows delivery.

- [ ] **Step 1: Write failing visibility tests**

Add handlers with all four flag combinations and assert:

```python
assert [tool["function"]["name"] for tool in build_model_tools(handlers)] == [
    "visible_tool"
]
```

Add an AI Chat input-building test where a resolved internal tool has
`deliver_result_to_model=False` and assert its ID is absent from
`pending_tool_results`, while the default Experience tool result remains present.

- [ ] **Step 2: Run the focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_tool_call_service.py apps/backend/tests/unit/test_experience_ai_chat.py -q
```

Expected: visibility assertions fail because both flags are undefined and no filtering exists.

- [ ] **Step 3: Implement capability declarations and filtering**

Add defaults to `ToolHandler`:

```python
model_visible: bool = True
deliver_result_to_model: bool = True
```

Filter model definitions in `build_model_tools()`. Add
`ToolCallService.should_deliver_result(tool_name: str) -> bool`, returning the bound
handler's flag, and use it while constructing `pending_tool_results`.

- [ ] **Step 4: Verify and commit**

Run the focused tests and Ruff, then commit:

```powershell
git add apps/backend/app/ai_chat/tools/handler.py apps/backend/app/ai_chat/streaming/model.py apps/backend/app/ai_chat/services/ai_chat_service.py apps/backend/tests/unit/test_tool_call_service.py apps/backend/tests/unit/test_experience_ai_chat.py
git commit -m "feat: declare Tool Call model delivery capabilities"
```

### Task 2: Add the `awaiting_input` Tool Call lifecycle

**Files:**
- Modify: `apps/backend/app/ai_chat/models/models.py`
- Modify: `apps/backend/app/ai_chat/tools/types.py`
- Modify: `apps/backend/app/ai_chat/repositories/tool_call_repository.py`
- Modify: `apps/backend/app/ai_chat/services/tool_call_service.py`
- Create: `apps/backend/app/scripts/migrate_ai_chat_tool_input_state.py`
- Modify: `apps/backend/app/db_engine.py`
- Modify: `apps/backend/tests/unit/test_tool_call_service.py`
- Modify: `apps/backend/tests/unit/test_experience_ai_chat.py`

**Interfaces:**
- Adds `awaiting_input` to `ToolCallStatus` and the database check constraint.
- Produces `ToolCallService.request_input(tool_call_id: int) -> ToolCall`.
- Produces `ToolCallService.resolve_input(tool_call_id: int, client_resolution_id: str, payload: JsonObject) -> ToolResult`.
- Produces `ToolCallService.find_awaiting_input(run_id: int) -> ToolCall | None`.

- [ ] **Step 1: Write failing lifecycle tests**

Test this exact sequence:

```python
call = await service.validate_call(
    context,
    '{"index":0,"provider_id":"input-1","name":"input_tool",'
    '"arguments":"{\\"batch_id\\":\\"b1\\"}"}',
)
waiting = await service.request_input(call["tool_call_id"])
assert waiting["status"] == "awaiting_input"
result = await service.resolve_input(waiting["tool_call_id"], "client-1", {"answers": []})
assert result.payload == {"answers": []}
```

Also assert same ID/payload replays, same ID/different payload conflicts, different ID conflicts, and two concurrent resolvers cannot both win.

- [ ] **Step 2: Run tests and verify the missing-state failure**

Run `test_tool_call_service.py`; expected failure is the missing API/status.

- [ ] **Step 3: Implement repository transitions**

Add repository methods with conditional updates:

```python
claim_input_request: validated -> awaiting_input
resolve_input: awaiting_input -> resolved
get_awaiting_input_for_run: select one row or raise on multiple rows
```

`resolve_input` writes `tool_result`, `delivery_status="pending"`,
`client_resolution_id`, and `resolved_at` atomically. Re-read the durable row after a lost race and compare strict JSON values.

- [ ] **Step 4: Add the idempotent SQLite migration**

Rebuild `ai_chat_tool_calls` so the status constraint accepts `awaiting_input`, preserving all rows, indexes, foreign keys, and existing approval data. Run migration twice in its test.

- [ ] **Step 5: Verify Experience compatibility and commit**

Run Tool Call and Experience tests, Ruff, and commit:

```powershell
git commit -m "feat: support Tool Calls awaiting external input"
```

### Task 3: Materialize system-owned Tool Calls with stable identities

**Files:**
- Modify: `apps/backend/app/ai_chat/repositories/tool_call_repository.py`
- Modify: `apps/backend/app/ai_chat/services/tool_call_service.py`
- Modify: `apps/backend/tests/unit/test_tool_call_service.py`

**Interfaces:**
- Produces:

```python
async def validate_system_call(
    self,
    context: ToolContext,
    *,
    identity: str,
    name: str,
    arguments: JsonObject,
) -> ToolCall: ...
```

- Produces `validate_model_call_as(context, raw_call, *, identity, expected_name)` for parsing model arguments while replacing provider identity and index with server values.

- [ ] **Step 1: Write failing stable-identity tests**

Assert the first call creates one row, the same identity/name/arguments replays it,
and changed name or strict JSON arguments raise `IdempotencyConflictError`.
Assert two different identities receive different `tool_call_index` values.

- [ ] **Step 2: Run tests and verify failure**

Expected: methods do not exist.

- [ ] **Step 3: Implement atomic index allocation and materialization**

Add repository lookup by `(run_id, provider_tool_call_id)` and allocate
`max(tool_call_index) + 1` inside the write transaction. On a unique-key race,
rollback and re-read by stable provider ID. Never accept the model-provided index or provider ID for these methods.

`validate_model_call_as()` must parse exactly one raw call, require
`expected_name`, extract only its arguments, then delegate to
`validate_system_call()`.

- [ ] **Step 4: Verify and commit**

Run the Tool Call suite and commit:

```powershell
git commit -m "feat: materialize system-owned Tool Calls"
```

### Task 4: Implement the JD question and persistence handlers

**Files:**
- Create: `apps/backend/app/jd_import/tools/__init__.py`
- Create: `apps/backend/app/jd_import/tools/ask_questions.py`
- Create: `apps/backend/app/jd_import/tools/persist_jd.py`
- Modify: `apps/backend/app/jd_import/adapters/adapter.py`
- Modify: `apps/backend/app/jd_import/agent/types.py`
- Modify: `apps/backend/app/jd_import/agent/questions.py`
- Create: `apps/backend/tests/unit/test_jd_import_tools.py`

**Interfaces:**
- Produces `AskJDQuestionsHandler` named `ask_jd_questions` with
  `model_visible=True`, `deliver_result_to_model=True`.
- Produces `PersistJDHandler` named `persist_jd` with
  `model_visible=False`, `deliver_result_to_model=False`, `security=LOW`.
- `JDImportAdapter.get_tool_handlers()` returns both handlers; node-level binding controls which one the model sees.

- [ ] **Step 1: Write failing `ask_jd_questions` validation tests**

Pass assessment, asked keys, round, and Run identity through
`ToolContext.adapter_context`. Assert validation creates server-owned batch and
question IDs, rejects more than 12 questions, a fourth round, duplicate keys,
unknown JD keys, and unknown fields.

- [ ] **Step 2: Write failing `persist_jd` transaction tests**

Validate one sanitized `CandidateJD`, execute with the injected session, and assert:

```python
assert result.payload == {"information_id": information.id}
assert information.status in {"confirmed", "incomplete"}
```

Force handler failure and verify both JD rows and Tool Call resolution roll back.

- [ ] **Step 3: Implement both handlers**

`AskJDQuestionsHandler.validation()` returns normalized batch arguments as trusted
payload; its `execute()` raises `ToolProtocolError` because input tools resolve via
`resolve_input()`.

`PersistJDHandler.validation()` rebuilds and validates `CandidateJD`. Its
`execute()` requires `context.session`, calls `JDImportRepository.create()`, and
returns only `information_id`.

- [ ] **Step 4: Register handlers and verify**

Run `test_jd_import_tools.py`, existing JD question/persistence tests, Ruff, and commit:

```powershell
git commit -m "feat: add JD Import Tool handlers"
```

### Task 5: Rewrite the JD Graph around real model Tool Calls

**Files:**
- Modify: `apps/backend/app/jd_import/graph/builder.py`
- Modify: `apps/backend/app/jd_import/graph/__init__.py`
- Modify: `apps/backend/app/jd_import/adapters/adapter.py`
- Modify: `apps/backend/app/jd_import/agent/state.py`
- Modify: `apps/backend/app/jd_import/agent/model.py`
- Modify: `apps/backend/app/jd_import/agent/prompts.py`
- Delete obsolete question-planning structures only after callers are removed.
- Modify: `apps/backend/tests/unit/test_jd_import_graph.py`
- Modify: `apps/backend/tests/unit/test_jd_import_adapter.py`

**Interfaces:**
- `build_jd_import_graph(runtime, deps)` receives the bound `AiChatRuntime`.
- `plan_questions` invokes `runtime.stream_model()` with only
  `ask_jd_questions` exposed.
- Graph checkpoint stores `question_tool_call_id`, not pending batch or raw answer.
- `persist` validates and executes one `persist_jd` Tool Call per candidate.

- [ ] **Step 1: Write failing no-tool and question-tool Graph tests**

For no Tool Call, assert trace `assess -> plan_questions -> persist`. For one model
Tool Call, assert stable identity `jd-import:questions:1`, event order
`jd.questions.requested -> _graph.interrupted`, and checkpoint contains only the
Tool Call ID.

- [ ] **Step 2: Write failing resume and extraction-loop tests**

Resolve the input Tool Call, resume with `{"tool_call_id": id}`, and assert
`merge_answers -> extract` occurs without `parse_input` or `resolve_urls`.
Assert the resolved answers become `user_answer` sources and reach the extraction model.

- [ ] **Step 3: Write failing persistence replay tests**

Assert one `persist_jd` Tool Call per candidate, partial failures produce stable
errors, and replay returns original `information_id` without duplicate JD rows.

- [ ] **Step 4: Implement node-level model binding and routes**

Construct planning messages from the complete assessment, source statuses, evidence
errors, question history, and round. Accept zero or one Tool Call; reject multiple
calls or a different tool name. Do not emit visible planning text.

Replace `GraphPersistence` use with `runtime.tools.validate_system_call()` and
`execute_call()`. Keep the existing server-side final incomplete-status derivation.

- [ ] **Step 5: Remove obsolete State and structured planning code**

Remove `pending_batch`, `resume_answer`, `QuestionPlanningRequest`, `QuestionPlan`,
and `LiteLLMJDImportModel.plan_questions()` after Graph callers are gone. Preserve
URL selection and extraction methods.

- [ ] **Step 6: Verify and commit**

Run all JD Graph, Adapter, model, question, and persistence tests, then commit:

```powershell
git commit -m "refactor: drive JD import side effects through Tool Calls"
```

### Task 6: Resume question input through Tool Calls and remove Run JSON

**Files:**
- Modify: `apps/backend/app/ai_chat/services/ai_chat_service.py`
- Modify: `apps/backend/app/ai_chat/graph/runner.py`
- Modify: `apps/backend/app/ai_chat/models/models.py`
- Modify: `apps/backend/app/ai_chat/repositories/run_repository.py`
- Delete: `apps/backend/app/scripts/migrate_ai_chat_run_result.py`
- Create: `apps/backend/app/scripts/migrate_remove_ai_chat_run_result.py`
- Modify: `apps/backend/app/db_engine.py`
- Modify: `apps/backend/tests/unit/test_ai_chat_run_result.py`
- Create or modify: `apps/backend/tests/unit/test_jd_import_resume.py`
- Modify: `apps/backend/tests/unit/test_experience_ai_chat.py`
- Modify: `apps/backend/tests/integration/test_jd_import_api.py`

**Interfaces:**
- `resolve_question_batch()` finds the suspended Run's unique `awaiting_input` Tool Call.
- The Graph resume value is `{"tool_call_id": int}`.
- `AiChatRun` has no `result_json` or JD-specific repository methods.

- [ ] **Step 1: Write failing service and API lifecycle tests**

Cover correct batch resolution, partial answer rejection, wrong batch conflict,
missing conversation, identical replay, conflicting replay, concurrent resolution,
second question round, cancellation after resolution, and completion.

- [ ] **Step 2: Replace Run-result reads and writes**

On interruption, verify the emitted batch identity matches the durable
`awaiting_input` Tool Call; do not copy the batch into Run. On resolve, call
`resolve_input()` before Graph resume. If resume is cancelled after resolution,
keep Run suspended so the same durable result can resume again.

- [ ] **Step 3: Remove generic Run business state**

Delete `AiChatRun.result_json`, `QuestionResolutionClaim`, `patch_result()`, and
`claim_question_resolution()`. Replace the old unit test with assertions that the
Run model contains only generic lifecycle fields.

- [ ] **Step 4: Add the removal migration**

Create an idempotent SQLite migration that drops `ai_chat_runs.result`. It need not
migrate JSON data because the JD Agent is not released. Wire it after the Tool input
state migration and update exact migration-name assertions.

- [ ] **Step 5: Run full targeted regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_tool_call_service.py apps/backend/tests/unit/test_experience_ai_chat.py apps/backend/tests/unit/test_jd_import_agent_domain.py apps/backend/tests/unit/test_jd_import_questions.py apps/backend/tests/unit/test_jd_import_model.py apps/backend/tests/unit/test_jd_import_tools.py apps/backend/tests/unit/test_jd_import_graph.py apps/backend/tests/unit/test_jd_import_adapter.py apps/backend/tests/unit/test_jd_import_resume.py apps/backend/tests/integration/test_jd_import_api.py -q
.\.venv\Scripts\python.exe -m ruff check apps/backend/app/ai_chat apps/backend/app/jd_import apps/backend/app/scripts/migrate_ai_chat_tool_input_state.py apps/backend/app/scripts/migrate_remove_ai_chat_run_result.py
git diff --check
```

- [ ] **Step 6: Commit the boundary cleanup**

```powershell
git add apps/backend/app/ai_chat apps/backend/app/jd_import apps/backend/app/scripts apps/backend/app/db_engine.py apps/backend/tests
git commit -m "refactor: keep JD durability in Tool Calls"
```

### Task 7: Final documentation and whole-backend verification

**Files:**
- Modify: `docs/superpowers/specs/2026-08-13-jd-import-agent-design.zh-CN.md`
- Modify: `docs/superpowers/specs/2026-08-14-jd-import-tool-call-design.zh-CN.md` only if implementation names differ from the approved interfaces.

**Interfaces:**
- Documentation describes Tool Call as the sole JD interaction/persistence ledger.

- [ ] **Step 1: Update the original JD design cross-reference**

Mark the Tool Call design as the superseding persistence/resume boundary and remove references to `AiChatRun.result_json`.

- [ ] **Step 2: Run the complete backend test suite**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests -q
```

Record the exact pass/fail count. Real LLM and real Playwright MCP calls remain opt-in.

- [ ] **Step 3: Run final static checks and inspect the worktree**

```powershell
.\.venv\Scripts\python.exe -m ruff check apps/backend/app apps/backend/tests
git diff --check
git status --short
```

- [ ] **Step 4: Commit documentation**

```powershell
git add docs/superpowers/specs/2026-08-13-jd-import-agent-design.zh-CN.md docs/superpowers/specs/2026-08-14-jd-import-tool-call-design.zh-CN.md
git commit -m "docs: finalize JD import Tool Call workflow"
```
