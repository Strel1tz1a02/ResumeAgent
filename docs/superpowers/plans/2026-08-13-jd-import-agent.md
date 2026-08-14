# JD Import Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one checkpointed LangGraph that accepts mixed text and URLs, extracts one or more JDs, asks at most three merged question batches, loops answers back through extraction, and persists every recognized JD as `confirmed` or `incomplete`.

**Architecture:** `JDImportAdapter` translates the existing AI Chat run into a `JDImportState`; a deterministic graph owns routing, limits, evidence checks, interrupts, and persistence, while an injectable structured model only makes semantic decisions. URL retrieval goes through an injectable Playwright MCP source provider behind an application URL policy and a mandatory network-egress boundary. Question resumption extends the shared GraphRunner/AiChatService without pretending that questions are Tool approvals.

**Tech Stack:** Python 3.13, FastAPI, Pydantic 2, SQLAlchemy async, SQLite, LangGraph 1.2.10, LiteLLM JSON completion, MCP Python SDK 2.0.0, Microsoft Playwright MCP, pytest.

## Global Constraints

- Keep one Graph: `parse_input -> resolve_urls -> extract -> assess -> plan_questions -> ask_questions -> merge_answers -> extract`, then `persist`.
- Mixed text and URLs are one input; detect at most 10 URLs and let the model choose at most 5 safe URLs to visit.
- One candidate JD stores at most one URL; possible many-URL-to-one-JD cases become questions.
- Required completeness is company, job name, and at least one validated requirement.
- Ask actively about optional `type` and `location`; asking is capped at 12 questions per batch and 3 batches total.
- Ask each stable `question_key` at most once; skipped or unknown answers count as answered.
- Persist all recognized candidates: `confirmed` when required facts and required conflicts are resolved, otherwise `incomplete`.
- Do not persist raw input, fetched page text, user-answer text, quotes, or evidence.
- `source_url` is nullable and non-unique; do not look up old JDs by URL.
- Never treat model confidence or `supported=true` as evidence; the server verifies every quote against its source.
- User answers are appended as sources and must return to `extract`; they never patch candidates directly.
- Question resumption is a separate protocol from Tool approval.
- Existing unrelated worktree changes must remain untouched; every task stages only its listed files.
- The stock Playwright MCP `allowed-origins`/`blocked-origins` settings are not a redirect security boundary. Production deployment must deny private/link-local/loopback/metadata-network egress outside the browser process; application code additionally validates initial and reported final URLs.

---

## File Map

**JD persistence**

- `app/jd_import/models/jd.py`: two-table ORM model only.
- `app/scripts/migrate_jd_import_origin.py`: idempotent SQLite rebuild from three tables to two.
- `app/jd_import/schemas/imports.py`: manual CRUD HTTP contracts.
- `app/jd_import/repositories/jd_repository.py`: aggregate CRUD and graph persistence primitives.
- `app/jd_import/services/jd_service.py`: manual CRUD rules.

**Agent domain**

- `app/jd_import/agent/types.py`: serializable source, evidence, candidate, conflict, question, answer, and result types.
- `app/jd_import/agent/state.py`: `JDImportState` only.
- `app/jd_import/agent/input_parser.py`: URL extraction, normalization, limits, and text-source creation.
- `app/jd_import/agent/evidence.py`: quote normalization and assessment.
- `app/jd_import/agent/questions.py`: stable keys, ordering, batching, and answer validation.
- `app/jd_import/agent/model.py`: `JDImportModel` protocol and production structured LiteLLM implementation.
- `app/jd_import/agent/prompts.py`: URL-selection, extraction, and question-planning prompts.
- `app/jd_import/sources/url_policy.py`: initial/final URL validation and DNS classification.
- `app/jd_import/sources/playwright_mcp.py`: allow-listed MCP calls and bounded result conversion.
- `app/jd_import/graph/builder.py`: graph nodes and edges only.
- `app/jd_import/adapters/adapter.py`: request binding and initial state construction.
- `app/jd_import/services/graph_persistence.py`: idempotent `jd_key -> information_id` persistence.

**Shared runtime and API**

- `app/ai_chat/models/models.py`: durable JSON `result` on `AiChatRun`.
- `app/scripts/migrate_ai_chat_run_result.py`: idempotent run-result migration.
- `app/ai_chat/repositories/run_repository.py`: result patch/read and question-resolution claim.
- `app/ai_chat/graph/runner.py`: generic JSON resume and checkpoint inspection.
- `app/ai_chat/services/ai_chat_service.py`: JD question suspension/resumption lifecycle.
- `app/jd_import/schemas/agent.py`: conversation/import/question HTTP schemas.
- `app/jd_import/routers/agent.py`: JD-specific SSE API.
- `app/main.py`: adapter registration and router wiring.
- `app/config.py`: Playwright MCP endpoint and hard limits.
- `apps/backend/pyproject.toml`: pin `mcp==2.0.0`.

---

### Task 1: Replace `jd_origin` with the final two-table aggregate

**Files:**
- Modify: `apps/backend/app/jd_import/models/jd.py`
- Modify: `apps/backend/app/jd_import/models/__init__.py`
- Modify: `apps/backend/app/jd_import/schemas/imports.py`
- Modify: `apps/backend/app/jd_import/repositories/jd_repository.py`
- Modify: `apps/backend/app/jd_import/services/jd_service.py`
- Create: `apps/backend/app/scripts/migrate_jd_import_origin.py`
- Modify: `apps/backend/app/db_engine.py`
- Modify: `apps/backend/tests/integration/test_jd_import_api.py`
- Create: `apps/backend/tests/unit/test_jd_import_migration.py`

**Interfaces:**
- Produces: `JDInformation(source_url, company, job_name, type, location, status, revision)` with `status in {incomplete, confirmed}`.
- Produces: `JDImportRepository.create(information_fields, requirements) -> JDInformation`.
- Produces: `JDImportService.create(JDImportCreate) -> JDImportResponse`; request has no `raw_text`.

- [ ] **Step 1: Change API tests first**

Replace create payloads with `source_url`, no `raw_text`, and assert both statuses:

```python
response = await client.post(
    "/api/v1/jd-imports",
    json={
        "source_url": "https://jobs.example/42",
        "company": "Acme",
        "job_name": "Backend Engineer",
        "status": "confirmed",
        "requirements": [{"priority": "required", "content": "Python"}],
    },
)
assert response.json()["source_url"] == "https://jobs.example/42"
assert "origin" not in response.json()
```

Add a test that two records may use the same URL and a test that `analysing` is rejected.

- [ ] **Step 2: Add migration tests**

Build a legacy SQLite schema with `jd_origin`, insert one aggregate, call `migrate(engine)` twice, then assert `jd_origin` is absent, `jd_information.source_url` contains the old URL, `raw_text` is absent, requirements survive, and migration is idempotent.

- [ ] **Step 3: Run focused tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/integration/test_jd_import_api.py apps/backend/tests/unit/test_jd_import_migration.py -q
```

Expected: failures mention `raw_text`, `JDOrigin`, old status constraint, or missing migration.

- [ ] **Step 4: Implement the model, repository, service, schemas, and migration**

The migration must rebuild `jd_information` because SQLite cannot rewrite check constraints in place:

```sql
CREATE TABLE jd_information_v2 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_url TEXT,
  company VARCHAR(200) NOT NULL DEFAULT '',
  job_name VARCHAR(200) NOT NULL DEFAULT '',
  type VARCHAR(100) NOT NULL DEFAULT '',
  location VARCHAR(200) NOT NULL DEFAULT '',
  status VARCHAR(16) NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0,
  CONSTRAINT ck_jd_information_status
    CHECK (status IN ('incomplete', 'confirmed')),
  CONSTRAINT ck_jd_information_revision CHECK (revision >= 0)
);
```

Copy `jd_origin.source_url` through the old foreign key, rebuild `jd_requirements` only if required to preserve its foreign key target, drop `jd_origin`, and record `2026_08_13_jd_import_origin` in `schema_migrations`. New databases must also work when no legacy tables exist.

Manual edits are allowed for both statuses; remove the former “confirmed must reopen to analysing” rule. Revision checks remain mandatory.

- [ ] **Step 5: Run tests and quality checks**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/integration/test_jd_import_api.py apps/backend/tests/unit/test_jd_import_migration.py -q
.\.venv\Scripts\python.exe -m ruff check apps/backend/app/jd_import apps/backend/app/scripts/migrate_jd_import_origin.py
git diff --check
```

Expected: all pass.

- [ ] **Step 6: Commit only Task 1 files**

```powershell
git add apps/backend/app/jd_import apps/backend/app/scripts/migrate_jd_import_origin.py apps/backend/app/db_engine.py apps/backend/tests/integration/test_jd_import_api.py apps/backend/tests/unit/test_jd_import_migration.py
git commit -m "refactor: finalize JD import persistence model"
```

---

### Task 2: Define serializable Agent state and deterministic input/evidence rules

**Files:**
- Create: `apps/backend/app/jd_import/agent/__init__.py`
- Create: `apps/backend/app/jd_import/agent/types.py`
- Create: `apps/backend/app/jd_import/agent/state.py`
- Create: `apps/backend/app/jd_import/agent/input_parser.py`
- Create: `apps/backend/app/jd_import/agent/evidence.py`
- Create: `apps/backend/tests/unit/test_jd_import_agent_domain.py`

**Interfaces:**
- Produces: `parse_mixed_input(raw_input: str, *, max_urls: int = 10) -> ParsedInput`.
- Produces: `assess_candidates(sources, candidates) -> Assessment`.
- Produces JSON-safe `JDImportState` with `input`, `sources`, `candidates`, `conflicts`, `questions`, and `result`.

- [ ] **Step 1: Write failing parsing/state tests**

Cover text plus URLs, punctuation trimming, case-normalized host/scheme, fragment removal, duplicate URLs, empty input, and 11 distinct URLs. Assert URL and remaining text sources coexist.

```python
parsed = parse_mixed_input("Apply: HTTPS://EXAMPLE.com/job#top\nPython role")
assert parsed.urls == ["https://example.com/job"]
assert parsed.text == "Apply:\nPython role"
```

Assert `json.dumps(initial_state)` succeeds.

- [ ] **Step 2: Write failing evidence tests**

Cover Unicode/whitespace normalization, bad `source_id`, unsupported quote removal, per-requirement evidence, required missing fields, optional missing fields, and unresolved ownership represented only in `conflicts`.

- [ ] **Step 3: Run tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_jd_import_agent_domain.py -q
```

Expected: import errors for the new modules.

- [ ] **Step 4: Implement exact JSON-safe types**

Use `TypedDict` for checkpoint state and Pydantic models for model/API validation. Required candidate facts use:

```python
class EvidenceFact(BaseModel):
    value: str
    source_id: str
    quote: str

class CandidateJD(BaseModel):
    jd_key: str
    source_url: EvidenceFact | None = None
    company: EvidenceFact | None = None
    job_name: EvidenceFact | None = None
    type: EvidenceFact | None = None
    location: EvidenceFact | None = None
    requirements: list[RequirementFact] = Field(default_factory=list)
```

Generate source IDs deterministically from source type plus ordinal. Do not store model confidence.

- [ ] **Step 5: Implement parsing and assessment**

`parse_mixed_input` raises `JDImportInputError("empty_input")` or `JDImportInputError("too_many_urls")`. `assess_candidates` returns sanitized candidates, missing-field descriptors, and conflicts without mutating input. A requirement counts only when content and quote validate.

- [ ] **Step 6: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_jd_import_agent_domain.py -q
.\.venv\Scripts\python.exe -m ruff check apps/backend/app/jd_import/agent apps/backend/tests/unit/test_jd_import_agent_domain.py
git add apps/backend/app/jd_import/agent apps/backend/tests/unit/test_jd_import_agent_domain.py
git commit -m "feat: add JD import agent domain state"
```

---

### Task 3: Add deterministic question planning and answer contracts

**Files:**
- Create: `apps/backend/app/jd_import/agent/questions.py`
- Modify: `apps/backend/app/jd_import/agent/types.py`
- Modify: `apps/backend/app/jd_import/agent/state.py`
- Create: `apps/backend/tests/unit/test_jd_import_questions.py`

**Interfaces:**
- Produces: `build_question_batch(assessment, asked_keys, round_number, model_questions) -> QuestionBatch | None`.
- Produces: `validate_batch_answer(batch, answer) -> list[ImportSource]`.
- Question kinds: `ownership | missing | conflict | source_access`.

- [ ] **Step 1: Write failing priority, limit, and dedupe tests**

Create more than 12 questions and assert exact order: ownership, required conflict, required missing, source access, optional conflict/missing. Assert only 12 are emitted, keys already asked never return, round 3 produces no batch, and choices always have `allow_custom=True` plus a skip path.

- [ ] **Step 2: Write failing answer-validation tests**

Assert batch mismatch, unknown question, missing answer, and duplicate question IDs fail. Assert a custom choice and a free-text answer create `user_answer` sources; skipped answers create no content source but remain answered.

- [ ] **Step 3: Run tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_jd_import_questions.py -q
```

- [ ] **Step 4: Implement stable keys and complete-batch validation**

Use keys such as `ownership:<fragment_hash>`, `missing:<jd_key>:company`, `conflict:<jd_key>:job_name:<values_hash>`, and `source_access:<url_hash>`. Batch and question IDs derive from `run_id`, round, and stable keys so checkpoint replay produces identical identities.

- [ ] **Step 5: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_jd_import_questions.py -q
.\.venv\Scripts\python.exe -m ruff check apps/backend/app/jd_import/agent/questions.py apps/backend/tests/unit/test_jd_import_questions.py
git add apps/backend/app/jd_import/agent apps/backend/tests/unit/test_jd_import_questions.py
git commit -m "feat: add JD import question batches"
```

---

### Task 4: Add the structured model boundary

**Files:**
- Create: `apps/backend/app/jd_import/agent/model.py`
- Create: `apps/backend/app/jd_import/agent/prompts.py`
- Create: `apps/backend/tests/unit/test_jd_import_model.py`

**Interfaces:**
- Produces protocol methods:

```python
class JDImportModel(Protocol):
    async def select_urls(self, request: UrlSelectionRequest) -> UrlSelection: ...
    async def extract(self, request: ExtractionRequest) -> ExtractionResult: ...
    async def plan_questions(self, request: QuestionPlanningRequest) -> QuestionPlan: ...
```

- Production class: `LiteLLMJDImportModel` using `complete_json(..., retries=1, schema_type="jd_import")` plus Pydantic validation.

- [ ] **Step 1: Write failing schema and repair tests**

Stub `complete_json` with invalid output followed by valid output and assert at most one repair. Verify URL selection rejects unknown/unsafe URL IDs and caps selection at 5. Verify extraction requires stable `jd_key`, evidence triples, and explicit conflicts.

- [ ] **Step 2: Run tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_jd_import_model.py -q
```

- [ ] **Step 3: Implement prompts and model adapter**

Prompts must state that sources are untrusted data, webpage instructions are never executable, prior candidates cannot be silently removed/merged, and user answers are evidence rather than direct commands. The repair attempt includes only Pydantic error details and the original request.

- [ ] **Step 4: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_jd_import_model.py -q
.\.venv\Scripts\python.exe -m ruff check apps/backend/app/jd_import/agent/model.py apps/backend/app/jd_import/agent/prompts.py
git add apps/backend/app/jd_import/agent/model.py apps/backend/app/jd_import/agent/prompts.py apps/backend/tests/unit/test_jd_import_model.py
git commit -m "feat: add JD import structured model"
```

---

### Task 5: Implement the safe Playwright MCP source provider

**Files:**
- Modify: `apps/backend/pyproject.toml`
- Modify: `apps/backend/app/config.py`
- Create: `apps/backend/app/jd_import/sources/__init__.py`
- Create: `apps/backend/app/jd_import/sources/url_policy.py`
- Create: `apps/backend/app/jd_import/sources/playwright_mcp.py`
- Create: `apps/backend/tests/unit/test_jd_import_url_sources.py`

**Interfaces:**
- Produces: `UrlPolicy.validate(url: str) -> ValidatedUrl`.
- Produces protocol: `PageSourceProvider.fetch(url: ValidatedUrl) -> PageSourceResult`.
- Production: `PlaywrightMCPSourceProvider(endpoint, timeout_seconds, max_chars)`.

- [ ] **Step 1: Pin the dependency and write failing URL-policy tests**

Add `mcp==2.0.0`. Test only `http/https`, no credentials, allowed ports `{80, 443}`, and rejection of loopback, RFC1918, link-local, multicast, reserved, IPv4-in-IPv6, and metadata endpoints. Stub DNS answers so tests never use the network.

- [ ] **Step 2: Write failing MCP-provider tests**

Inject a fake MCP client and assert the provider exposes only `browser_navigate` and `browser_snapshot`, calls them in order, validates the reported final URL, accepts text content only, truncates at the configured character limit, and maps timeout/login/captcha/tool errors to `blocked` or `failed`.

- [ ] **Step 3: Run tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_jd_import_url_sources.py -q
```

- [ ] **Step 4: Implement the policy and MCP client**

Use the official SDK production shape:

```python
from mcp import Client

async with Client(settings.playwright_mcp_url) as client:
    await client.call_tool("browser_navigate", {"url": validated.url})
    snapshot = await client.call_tool("browser_snapshot", {})
```

Reject any server missing those two tools. Never expose `browser_run_code`, click, form, filesystem, network-body, or arbitrary tool names. Set defaults: connect/call timeout 20 seconds, max page text 100,000 characters. `PLAYWRIGHT_MCP_URL` is required only when the graph selects a URL.

Document in `config.py` comments and deployment error text that private-network egress blocking is mandatory because Playwright MCP origin flags do not secure redirects. If the environment cannot provide that boundary, URL fetching must fail closed with `source_security_unavailable` while text import remains usable.

- [ ] **Step 5: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_jd_import_url_sources.py -q
.\.venv\Scripts\python.exe -m ruff check apps/backend/app/jd_import/sources apps/backend/app/config.py
git add apps/backend/pyproject.toml apps/backend/app/config.py apps/backend/app/jd_import/sources apps/backend/tests/unit/test_jd_import_url_sources.py
git commit -m "feat: add safe Playwright MCP JD sources"
```

---

### Task 6: Add durable run results for question and persistence idempotency

**Files:**
- Modify: `apps/backend/app/ai_chat/models/models.py`
- Modify: `apps/backend/app/ai_chat/repositories/run_repository.py`
- Create: `apps/backend/app/scripts/migrate_ai_chat_run_result.py`
- Modify: `apps/backend/app/db_engine.py`
- Create: `apps/backend/tests/unit/test_ai_chat_run_result.py`

**Interfaces:**
- Adds `AiChatRun.result_json: dict` mapped to SQL column `result`.
- Produces:

```python
async def patch_result(self, run_id: int, patch: JsonObject) -> JsonObject: ...
async def claim_question_resolution(
    self, run_id: int, batch_id: str, client_resolution_id: str
) -> QuestionResolutionClaim: ...
```

- Run result owns `pending_question_batch`, `question_resolutions`, and `persisted_jds` (`jd_key -> information_id`).

- [ ] **Step 1: Write failing migration and repository tests**

Assert migration twice is safe, JSON round-trips, the same `(batch_id, client_resolution_id)` replays, a different client ID for an already resolved batch conflicts, and atomic claims cannot both win.

- [ ] **Step 2: Run tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_ai_chat_run_result.py -q
```

- [ ] **Step 3: Implement JSON result migration and compare-and-set updates**

Migration adds `result JSON NOT NULL DEFAULT '{}'`. Repository mutations load, validate, and update inside the caller transaction; `claim_question_resolution` must use a conditional update or SQLite write transaction so duplicate concurrent resolves are deterministic.

- [ ] **Step 4: Verify shared regressions and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_ai_chat_run_result.py apps/backend/tests/unit/test_experience_ai_chat.py -q
.\.venv\Scripts\python.exe -m ruff check apps/backend/app/ai_chat/models/models.py apps/backend/app/ai_chat/repositories/run_repository.py apps/backend/app/scripts/migrate_ai_chat_run_result.py
git add apps/backend/app/ai_chat/models/models.py apps/backend/app/ai_chat/repositories/run_repository.py apps/backend/app/scripts/migrate_ai_chat_run_result.py apps/backend/app/db_engine.py apps/backend/tests/unit/test_ai_chat_run_result.py
git commit -m "feat: persist AI chat run results"
```

---

### Task 7: Implement idempotent multi-JD persistence

**Files:**
- Create: `apps/backend/app/jd_import/services/graph_persistence.py`
- Modify: `apps/backend/app/jd_import/repositories/jd_repository.py`
- Modify: `apps/backend/app/ai_chat/repositories/run_repository.py`
- Create: `apps/backend/tests/unit/test_jd_import_graph_persistence.py`

**Interfaces:**
- Produces: `GraphPersistence.persist(run_id: int, candidates: list[CandidateJD]) -> ImportResult`.
- Consumes/updates `AiChatRun.result_json["persisted_jds"]`.

- [ ] **Step 1: Write failing transaction and replay tests**

Test complete and incomplete candidates in one run, a forced failure for one JD, successful continuation for the next JD, and replay returning original IDs without duplicate rows.

- [ ] **Step 2: Run tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_jd_import_graph_persistence.py -q
```

- [ ] **Step 3: Implement one transaction per candidate**

Before insert, read the durable mapping. Insert JD and requirements, then write `jd_key -> id` in the same database transaction. Derive status server-side from sanitized required facts and unresolved required conflicts. Catch per-candidate database errors into stable error objects `{jd_key, code}`; never expose SQL text.

- [ ] **Step 4: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_jd_import_graph_persistence.py -q
.\.venv\Scripts\python.exe -m ruff check apps/backend/app/jd_import/services/graph_persistence.py
git add apps/backend/app/jd_import/services/graph_persistence.py apps/backend/app/jd_import/repositories/jd_repository.py apps/backend/app/ai_chat/repositories/run_repository.py apps/backend/tests/unit/test_jd_import_graph_persistence.py
git commit -m "feat: persist JD graph results idempotently"
```

---

### Task 8: Build the single JD import Graph and Adapter

**Files:**
- Create: `apps/backend/app/jd_import/graph/__init__.py`
- Create: `apps/backend/app/jd_import/graph/builder.py`
- Create: `apps/backend/app/jd_import/adapters/__init__.py`
- Create: `apps/backend/app/jd_import/adapters/adapter.py`
- Modify: `apps/backend/app/jd_import/__init__.py`
- Create: `apps/backend/tests/unit/test_jd_import_graph.py`
- Create: `apps/backend/tests/unit/test_jd_import_adapter.py`

**Interfaces:**
- Produces: `build_jd_import_graph(deps: JDImportGraphDependencies) -> StateGraph`.
- Produces: `JDImportAdapter(BaseAdapter[JDImportState])` accepting only subject `jd_import/new`, empty scope, and no model Tool handlers.

- [ ] **Step 1: Write failing Adapter tests**

Assert valid binding normalizes to `{type: "jd_import", id: "new"}`, rejects any other subject or non-empty scope, consumes only the current run's user content, and initializes round `0`, empty candidates/conflicts/questions/result.

- [ ] **Step 2: Write failing Graph happy-path test**

Use Fake Model, Fake Page Provider, in-memory SQLite checkpoint, and Fake Persistence. Assert node trace is `parse_input, resolve_urls, extract, assess, persist`, events end with `jd.import.completed`, and mixed text/URL reach extraction.

- [ ] **Step 3: Write failing interrupt-loop test**

First extraction returns missing company; assert `jd.questions.requested` precedes `_graph.interrupted`. Resume with the full batch and assert the trace contains `merge_answers, extract` and does not return to `parse_input` or `resolve_urls`. Assert a second new batch is allowed and the fourth is not.

- [ ] **Step 4: Write failing conflict and partial-failure tests**

Cover multiple JDs, ambiguous ownership, two URLs for one JD, optional questions, source failure, quote rejection, prompt injection text, model repair failure, and partial persistence errors.

- [ ] **Step 5: Run tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_jd_import_graph.py apps/backend/tests/unit/test_jd_import_adapter.py -q
```

- [ ] **Step 6: Implement nodes and routing**

`ask_questions` must emit the durable batch before interrupt:

```python
_emit("jd.questions.requested", batch.model_dump(mode="json"))
answer = QuestionBatchAnswer.model_validate(
    interrupt({"type": "question_batch", "batch_id": batch.batch_id})
)
return {"resume_answer": answer.model_dump(mode="json")}
```

The interrupting node must have no non-idempotent side effects before `interrupt()`. Increment round and store asked keys in State returned by `plan_questions`, not inside `ask_questions`. `merge_answers` validates and appends sources, clears the pending batch, then has one unconditional edge to `extract`.

- [ ] **Step 7: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_jd_import_graph.py apps/backend/tests/unit/test_jd_import_adapter.py -q
.\.venv\Scripts\python.exe -m ruff check apps/backend/app/jd_import/graph apps/backend/app/jd_import/adapters
git add apps/backend/app/jd_import/graph apps/backend/app/jd_import/adapters apps/backend/app/jd_import/__init__.py apps/backend/tests/unit/test_jd_import_graph.py apps/backend/tests/unit/test_jd_import_adapter.py
git commit -m "feat: add JD import extraction graph"
```

---

### Task 9: Extend the shared runtime with typed question resume

**Files:**
- Modify: `apps/backend/app/ai_chat/graph/runner.py`
- Modify: `apps/backend/app/ai_chat/services/ai_chat_service.py`
- Modify: `apps/backend/app/ai_chat/types/adapter_input.py`
- Modify: `apps/backend/tests/unit/test_experience_ai_chat.py`
- Create: `apps/backend/tests/unit/test_jd_import_resume.py`

**Interfaces:**
- Produces: `GraphRunner.resume_value(adapter_name, conversation_id, value: JsonObject)`.
- Produces: `GraphRunner.get_state(adapter_name, conversation_id) -> GraphStateSnapshot`.
- Produces: `AiChatService.resolve_question_batch(conversation_id, batch_id, answer) -> AsyncIterator[AiChatEvent]`.

- [ ] **Step 1: Preserve approval behavior with characterization tests**

Add assertions that Experience still requires `proposal.requested` before suspension and `resolve_proposal` still filters its replayed proposal event. These must pass before changing runtime code.

- [ ] **Step 2: Write failing JD suspension/resume lifecycle tests**

Assert `_execute` accepts `jd.questions.requested` as the business event paired with `_graph.interrupted`, stores the pending batch in run result, and marks the run suspended. Assert resolve verifies adapter, run, batch and whole answer, claims the resolution, transitions suspended->running, and supports either completion or another `jd.questions.requested` + suspension.

- [ ] **Step 3: Write failing idempotency/cancellation tests**

Cover replay of identical client resolution, conflicting ID/payload, concurrent resolve, cancellation after claim returning run to suspended, graph failure marking failed, and no duplicate extraction on replay.

- [ ] **Step 4: Run tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_experience_ai_chat.py apps/backend/tests/unit/test_jd_import_resume.py -q
```

- [ ] **Step 5: Implement generic runner primitives and JD-specific service lifecycle**

Keep `resume(...)` as the approval wrapper for backward compatibility; implement it by calling `resume_value(...)`. Do not make generic `_execute` accept arbitrary pre-interrupt events: allow exactly `proposal.requested` and `jd.questions.requested`, and verify identity against the interrupt/checkpoint state.

On a second question round, transition `running -> suspended` and yield the new question event. On completion, transition `running -> completed` and yield graph completion events. Do not create a second AI Chat Run for question answers.

- [ ] **Step 6: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_experience_ai_chat.py apps/backend/tests/unit/test_jd_import_resume.py -q
.\.venv\Scripts\python.exe -m ruff check apps/backend/app/ai_chat/graph/runner.py apps/backend/app/ai_chat/services/ai_chat_service.py
git add apps/backend/app/ai_chat/graph/runner.py apps/backend/app/ai_chat/services/ai_chat_service.py apps/backend/app/ai_chat/types/adapter_input.py apps/backend/tests/unit/test_experience_ai_chat.py apps/backend/tests/unit/test_jd_import_resume.py
git commit -m "feat: resume AI chat question batches"
```

---

### Task 10: Expose JD Agent API and register production dependencies

**Files:**
- Create: `apps/backend/app/jd_import/schemas/agent.py`
- Modify: `apps/backend/app/jd_import/schemas/__init__.py`
- Create: `apps/backend/app/jd_import/routers/agent.py`
- Modify: `apps/backend/app/jd_import/routers/__init__.py`
- Modify: `apps/backend/app/jd_import/__init__.py`
- Modify: `apps/backend/app/main.py`
- Create: `apps/backend/tests/integration/test_jd_import_agent_api.py`

**Interfaces:**
- `POST /api/v1/jd-imports/conversations`.
- `POST /api/v1/jd-imports/conversations/{id}/imports` with `{content, client_message_id}`.
- `POST /api/v1/jd-imports/conversations/{id}/question-batches/{batch_id}/resolve` with `{type, client_resolution_id, answers}`.

- [ ] **Step 1: Write failing conversation/import API tests**

Assert conversation creation returns 201 and a conversation ID; import rejects blank content and more than 10 URLs with 422; a valid import streams SSE preamble and stable business events. Verify manual CRUD routes remain reachable and dynamic routes are registered before `/{information_id}` to avoid path capture.

- [ ] **Step 2: Write failing question-resolve API tests**

Assert full batch resume, partial answer 422, wrong batch 409, missing conversation 404, duplicate resolution replay, and second-round SSE. Graph-level errors map to `jd.import.failed` without leaking exception text.

- [ ] **Step 3: Run tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/integration/test_jd_import_agent_api.py -q
```

- [ ] **Step 4: Implement schemas/router and register Adapter**

Create the long-lived production dependencies in `_register_business_adapters()`:

```python
register_adapter(
    JDImportAdapter(
        model=LiteLLMJDImportModel(),
        page_sources=PlaywrightMCPSourceProvider.from_settings(),
        persistence=GraphPersistence(database_module.db.session),
    )
)
```

Router error mapping must distinguish request validation, conversation/run state conflict, and infrastructure failure. Reuse the existing SSE encoding/preamble behavior through a shared helper or a focused local equivalent; do not import private functions from the Experience router.

- [ ] **Step 5: Verify and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/integration/test_jd_import_agent_api.py apps/backend/tests/integration/test_jd_import_api.py -q
.\.venv\Scripts\python.exe -m ruff check apps/backend/app/jd_import apps/backend/app/main.py
git add apps/backend/app/jd_import apps/backend/app/main.py apps/backend/tests/integration/test_jd_import_agent_api.py
git commit -m "feat: expose JD import agent API"
```

---

### Task 11: Full regression and controlled MCP smoke test

**Files:**
- Create: `apps/backend/tests/smoke/test_playwright_mcp_jd_import.py`
- Create: `apps/backend/docs/jd-import-playwright-mcp.md`

**Interfaces:**
- Smoke test is skipped unless `PLAYWRIGHT_MCP_URL` and `JD_IMPORT_MCP_SMOKE_URL` are explicitly set.

- [ ] **Step 1: Add the opt-in smoke test and deployment guide**

The smoke target must be a user-controlled public fixture. The test validates endpoint/tool discovery, navigate/snapshot conversion, final URL policy, and bounded output. The guide must state required network egress denial, allowed tools, timeouts, environment variables, and why MCP origin flags alone are insufficient.

- [ ] **Step 2: Run all JD and shared AI Chat suites**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit/test_jd_import_*.py apps/backend/tests/integration/test_jd_import_*.py apps/backend/tests/unit/test_experience_ai_chat.py -q
```

Expected: all pass without network or real model access.

- [ ] **Step 3: Run full backend regression**

```powershell
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/unit -q
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/integration -q
.\.venv\Scripts\python.exe -m ruff check apps/backend/app apps/backend/tests/unit/test_jd_import_*.py apps/backend/tests/integration/test_jd_import_*.py
git diff --check
```

Expected: all existing and new tests pass. If unrelated pre-existing failures exist, record exact test IDs and verify the JD-focused suites remain green; do not weaken assertions.

- [ ] **Step 4: Run the opt-in smoke test only in a secured environment**

```powershell
$env:PLAYWRIGHT_MCP_URL='http://127.0.0.1:8931/mcp'
$env:JD_IMPORT_MCP_SMOKE_URL='https://fixture.example/jobs/backend'
.\.venv\Scripts\python.exe -m pytest apps/backend/tests/smoke/test_playwright_mcp_jd_import.py -q
```

Expected: one successful bounded fetch. Do not run against arbitrary third-party job sites.

- [ ] **Step 5: Commit verification artifacts**

```powershell
git add apps/backend/tests/smoke/test_playwright_mcp_jd_import.py apps/backend/docs/jd-import-playwright-mcp.md
git commit -m "test: verify JD import agent workflow"
```

---

## Completion Criteria

- The Graph demonstrably loops `merge_answers -> extract` for every resolved batch.
- One import can persist multiple independent JDs, including mixed `confirmed` and `incomplete` results.
- All question limits, ordering, dedupe, full-batch validation, and three-round termination are server-enforced.
- Every accepted extracted fact passed deterministic source-quote verification during the run.
- No raw text, webpage body, answer text, or quote remains in JD business tables after completion.
- URL fetching fails closed without a configured network security boundary and uses only the two allow-listed Playwright MCP tools.
- Checkpoint replay and duplicate resolution do not create duplicate JD rows.
- Experience Tool approval tests remain unchanged in behavior.
