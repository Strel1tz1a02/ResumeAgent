# Personal Experience Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved person-level experience library in Resume-Matcher, including immediate free-text persistence, structured manual editing, ordered action/result/metrics evidence, completeness, archive/permanent deletion, and stateless AI questioning.

**Architecture:** Add `ExperienceItem` and `EvidenceItem` to the existing SQLAlchemy metadata, expose a public shared-session boundary, and keep all feature queries in dedicated repositories. FastAPI routers delegate transactions and invariants to focused services. A new Next.js `/experiences` workspace consumes the existing centralized API client and keeps existing resumes fully decoupled.

**Tech Stack:** Python 3.13, FastAPI 0.128, Pydantic 2, async SQLAlchemy 2, SQLite/aiosqlite, pytest/httpx; Next.js 16, React 19, TypeScript 5, Tailwind 4, Vitest/Testing Library, existing Swiss design system and six-locale i18n.

## Global Constraints

- Implement only in the `Resume-Matcher` repository.
- Do not import parsed experiences from the master resume.
- `POST /api/v1/experiences/import-text` must commit the raw user text before any LLM request; it must not call the LLM.
- Experiences are person-level records with auto-incrementing integer IDs and no `resume_id`, source/provenance columns, `date_text`, or user ownership column in this local single-user phase.
- Store ordered evidence references as unique `evidence_ids: JSON[list[int]]`; each evidence is owned by exactly one experience and is not shared.
- Completeness is a persisted server-computed integer from 0 to 100; clients never set it.
- Existing resumes are never changed by experience edits, archive, restore, or permanent deletion.
- Archive is the default delete; permanent deletion is available only for archived records and deletes owned evidence transactionally.
- New persistence operations live in `app/experience/repositories/experience_repository.py` and `app/experience/repositories/evidence_repository.py`, not in `database.py`; `database.py` may only expose shared session plumbing.
- Do not add migration tooling or migration scripts; fresh development databases use current metadata `create_all`.
- AI writes only typed patches through the application service and never writes the database directly.
- Do not persist AI conversation history.
- Reuse `apps/frontend/lib/api/client.ts`; do not add a second API-base or timeout implementation.
- All static strings must be added with identical key structure to `en`, `es`, `fr`, `ja`, `pt-BR`, and `zh` locale files.
- Follow TDD for every behavior: observe the targeted test fail before production code, then make it pass.
- Preserve unrelated working-tree changes.

---

### Task 1: Domain models, schemas, and completeness

**Files:**
- Modify: `apps/backend/app/models.py`
- Create: `apps/backend/app/experience/schemas/experiences.py`
- Create: `apps/backend/app/experience/schemas/evidence_items.py`
- Create: `apps/backend/app/experience/services/experience_completeness_service.py`
- Create: `apps/backend/tests/unit/test_experience_completeness.py`
- Create: `apps/backend/tests/unit/test_experience_schemas.py`

**Interfaces:**
- Consumes: existing `Base`, `_utcnow_iso`, Pydantic 2 validators, SQLAlchemy JSON columns.
- Produces: `ExperienceItem`, `EvidenceItem`, `ExperienceKind`, `ExperienceStatus`, request/response schemas, `READY_COMPLETENESS_THRESHOLD = 60`, and `calculate_completeness(experience, evidence_items) -> CompletenessResult`.

- [ ] **Step 1: Write failing completeness tests**

```python
def test_complete_experience_scores_100() -> None:
    experience = SimpleNamespace(
        kind="project", title="Recruiting Assistant", organization="Personal project",
        role="Backend developer", start_date="2026-07", end_date="2026-08",
        is_current=False, background="Students needed organized job data",
    )
    evidence = [SimpleNamespace(action="Built APIs", result="Unified sources", metrics="5 sources")]
    result = calculate_completeness(experience, evidence)
    assert result.completeness == 100
    assert result.missing_dimensions == []

def test_placeholder_title_and_missing_facts_do_not_score() -> None:
    experience = SimpleNamespace(
        kind="other", title="Untitled experience", organization=None, role=None,
        start_date=None, end_date=None, is_current=False, background=None,
    )
    result = calculate_completeness(experience, [])
    assert result.completeness == 0
    assert "identity" in result.missing_dimensions
```

- [ ] **Step 2: Run the completeness tests and verify RED**

Run: `cd apps/backend && uv run pytest tests/unit/test_experience_completeness.py -q`

Expected: collection fails because `experience_completeness_service` does not exist.

- [ ] **Step 3: Implement the pure completeness service**

Implement named constants for the eight scoring dimensions, placeholder-title detection for empty/`Untitled experience`, deterministic localized-neutral question keys, and:

```python
@dataclass(frozen=True)
class CompletenessResult:
    completeness: int
    missing_dimensions: list[str]
    suggested_questions: list[str]

def calculate_completeness(
    experience: ExperienceLike,
    evidence_items: Sequence[EvidenceLike],
) -> CompletenessResult: ...
```

Dates earn points only for start+end or start+`is_current`; evidence dimensions may be satisfied by different evidence rows.

- [ ] **Step 4: Run completeness tests and verify GREEN**

Run: `cd apps/backend && uv run pytest tests/unit/test_experience_completeness.py -q`

Expected: all completeness tests pass.

- [ ] **Step 5: Write failing schema tests**

Cover accepted experience kinds/statuses, `YYYY-MM` dates, `is_current`/`end_date` conflict, list normalization/deduplication, client rejection of `completeness`, nonblank `action`, and response aggregate fields.

```python
def test_current_experience_rejects_end_date() -> None:
    with pytest.raises(ValidationError):
        ExperienceCreate(kind="work", title="Engineer", is_current=True, end_date="2026-07")

def test_client_cannot_set_completeness() -> None:
    with pytest.raises(ValidationError):
        ExperienceUpdate.model_validate({"title": "X", "completeness": 99})
```

- [ ] **Step 6: Run schema tests and verify RED**

Run: `cd apps/backend && uv run pytest tests/unit/test_experience_schemas.py -q`

Expected: collection fails because the schema modules do not exist.

- [ ] **Step 7: Add ORM models and strict Pydantic schemas**

Add `ExperienceItem` and `EvidenceItem` exactly as specified, with indexes on status, kind, and updated timestamp. Define separate create, patch, read, detail, list-query, completeness, import-text, ready-conflict, deletion-impact, evidence-create/patch/reorder schemas. Set every request model to `ConfigDict(extra="forbid")` so server-owned fields cannot be injected.

- [ ] **Step 8: Run focused tests and existing model tests**

Run: `cd apps/backend && uv run pytest tests/unit/test_experience_schemas.py tests/unit/test_experience_completeness.py tests/unit/test_database.py -q`

Expected: all selected tests pass and existing ORM initialization remains valid.

- [ ] **Step 9: Commit Task 1**

```bash
git add apps/backend/app/models.py apps/backend/app/experience/schemas/experiences.py apps/backend/app/experience/schemas/evidence_items.py apps/backend/app/experience/services/experience_completeness_service.py apps/backend/tests/unit/test_experience_completeness.py apps/backend/tests/unit/test_experience_schemas.py
git commit -m "feat: define experience library domain"
```

---

### Task 2: Shared session boundary and repositories

**Files:**
- Modify: `apps/backend/app/database.py`
- Create: `apps/backend/app/repositories/__init__.py`
- Create: `apps/backend/app/repositories/session.py`
- Create: `apps/backend/app/experience/repositories/experience_repository.py`
- Create: `apps/backend/app/experience/repositories/evidence_repository.py`
- Create: `apps/backend/tests/unit/test_experience_repositories.py`

**Interfaces:**
- Consumes: `Database._session`, ORM models from Task 1, async SQLAlchemy `AsyncSession`.
- Produces: `Database.session()`, `get_repository_session()`, `ExperienceRepository`, and `EvidenceRepository`. Repositories never commit; the owning service controls commit/rollback.

- [ ] **Step 1: Write failing real-SQLite repository tests**

Use a `Database(tmp_path / "experience.db")`, open `async with database.session() as session`, create repositories with that same session, and test create/get/list/search/archive visibility and evidence order expansion.

```python
async def test_repositories_preserve_evidence_order(tmp_path) -> None:
    database = Database(db_path=tmp_path / "experience.db")
    async with database.session() as session:
        experiences = ExperienceRepository(session)
        evidence = EvidenceRepository(session)
        item = await experiences.create(ExperienceItem(kind="project", title="Agent"))
        first = await evidence.create(EvidenceItem(action="First"))
        second = await evidence.create(EvidenceItem(action="Second"))
        await experiences.set_evidence_ids(item.experience_id, [second.id, first.id])
        await session.commit()
        assert [row.id for row in await evidence.list_for_experience(item.experience_id)] == [second.id, first.id]
```

- [ ] **Step 2: Run repository tests and verify RED**

Run: `cd apps/backend && uv run pytest tests/unit/test_experience_repositories.py -q`

Expected: collection fails because repository modules and public session boundary do not exist.

- [ ] **Step 3: Expose only shared session plumbing from `Database`**

Add an `@asynccontextmanager` public method:

```python
@asynccontextmanager
async def session(self) -> AsyncIterator[AsyncSession]:
    async with self._session() as session:
        yield session
```

Do not add experience CRUD to `Database`.

- [ ] **Step 4: Implement repositories**

`ExperienceRepository` provides typed methods `create`, `get`, `list`, `update_fields`, `set_evidence_ids`, and `delete`. Its list method accepts `q`, `kind`, `status`, and `sort`, defaulting to draft+ready and searching the approved columns. `EvidenceRepository` provides `create`, `get`, `list_for_experience`, `get_for_experience`, `update_fields`, `delete`, and `find_owner_experience_id`; evidence ownership and order are enforced by `experience_evidence_items`.

`get_repository_session()` dynamically imports `app.database.db` inside the dependency so the existing `isolated_db` monkeypatch remains authoritative:

```python
async def get_repository_session() -> AsyncIterator[AsyncSession]:
    from app.database import db
    async with db.session() as session:
        yield session
```

- [ ] **Step 5: Run repository tests and verify GREEN**

Run: `cd apps/backend && uv run pytest tests/unit/test_experience_repositories.py tests/unit/test_database.py -q`

Expected: all selected tests pass against real temporary SQLite.

- [ ] **Step 6: Commit Task 2**

```bash
git add apps/backend/app/database.py apps/backend/app/repositories apps/backend/tests/unit/test_experience_repositories.py
git commit -m "feat: add experience repositories"
```

---

### Task 3: Text import and manual experience CRUD API

**Files:**
- Create: `apps/backend/app/experience/services/experience_import_service.py`
- Create: `apps/backend/app/experience/services/experience_service.py`
- Create: `apps/backend/app/experience/routers/experiences.py`
- Modify: `apps/backend/app/main.py`
- Create: `apps/backend/tests/integration/test_experiences_api.py`

**Interfaces:**
- Consumes: Task 1 schemas/completeness, Task 2 repositories/session dependency.
- Produces: `/api/v1/experiences` list/create/detail/patch and `/api/v1/experiences/import-text`, returning expanded `ExperienceDetail` responses.

- [ ] **Step 1: Write failing API tests for immediate import and CRUD**

Use `ASGITransport(app=app)` with `isolated_db`. Patch the existing `app.llm.complete_json` boundary and assert it is never called, then assert import returns `201` and persists exact `raw_input`. Cover blank/oversized `422`, manual create, detail, patch, and `completeness` injection rejection.

```python
async def test_import_text_persists_without_llm(isolated_db) -> None:
    with patch("app.llm.complete_json") as llm:
        async with _client() as client:
            response = await client.post("/api/v1/experiences/import-text", json={"text": "Built a campus recruiting assistant."})
    assert response.status_code == 201
    assert response.json()["raw_input"] == "Built a campus recruiting assistant."
    llm.assert_not_called()
```

- [ ] **Step 2: Run API tests and verify RED**

Run: `cd apps/backend && uv run pytest tests/integration/test_experiences_api.py -q`

Expected: requests return 404 because the router is not mounted.

- [ ] **Step 3: Implement import and application services**

`ExperienceImportService.import_text(text)` creates `kind=other`, empty title, draft status, empty evidence, exact raw text, computes completeness, commits, and returns expanded detail. `ExperienceService` owns create/get/list/patch, field normalization, completeness recalculation, and detail assembly. Repository exceptions are translated into domain exceptions, not HTTP exceptions.

- [ ] **Step 4: Implement and mount the router**

Create router prefix `/experiences`, inject one shared session per request, instantiate repositories/services, map domain errors to `404`, `409`, and `422`, and mount it in `main.py` under `/api/v1`.

- [ ] **Step 5: Run API tests and verify GREEN**

Run: `cd apps/backend && uv run pytest tests/integration/test_experiences_api.py -q`

Expected: import and CRUD cases pass using the real temporary database.

- [ ] **Step 6: Run adjacent integration tests**

Run: `cd apps/backend && uv run pytest tests/integration/test_resume_api.py tests/integration/test_resume_wizard_api.py tests/integration/test_experiences_api.py -q`

Expected: all selected tests pass; resume behavior is unchanged.

- [ ] **Step 7: Commit Task 3**

```bash
git add apps/backend/app/experience/services/experience_import_service.py apps/backend/app/experience/services/experience_service.py apps/backend/app/experience/routers/experiences.py apps/backend/app/main.py apps/backend/tests/integration/test_experiences_api.py
git commit -m "feat: add experience text import and CRUD"
```

---

### Task 4: Transactional evidence operations

**Files:**
- Create: `apps/backend/app/experience/services/evidence_service.py`
- Modify: `apps/backend/app/experience/routers/experiences.py`
- Modify: `apps/backend/app/experience/services/experience_service.py`
- Modify: `apps/backend/tests/unit/test_experience_repositories.py`
- Modify: `apps/backend/tests/integration/test_experiences_api.py`

**Interfaces:**
- Consumes: repository methods and expanded responses from Tasks 2–3.
- Produces: evidence create/patch/delete/reorder endpoints, atomic relation-table ownership/order maintenance, and automatic completeness/status updates.

- [ ] **Step 1: Add failing transaction and API tests**

Cover create appends ID, patch updates only owned evidence, cross-experience access returns `404`, delete removes row and ID, reorder requires the exact current unique ID set, and a forced failure rolls back both evidence and experience changes.

```python
async def test_reorder_rejects_missing_or_duplicate_ids(isolated_db) -> None:
    # create experience + two evidence rows through the API
    response = await client.put(
        f"/api/v1/experiences/{experience_id}/evidence-order",
        json={"evidence_ids": [first_id, first_id]},
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `cd apps/backend && uv run pytest tests/integration/test_experiences_api.py -q -k evidence`

Expected: evidence routes return 404.

- [ ] **Step 3: Implement `EvidenceService` transaction rules**

All operations accept the same session/repositories used by `ExperienceService`. After every create/patch/delete/reorder, load ordered evidence, recompute and persist completeness, return ready experiences to draft when below 60, and commit once. On any exception, rollback and re-raise a domain error.

- [ ] **Step 4: Add evidence routes**

Implement:

```http
POST   /experiences/{experience_id}/evidence
PATCH  /experiences/{experience_id}/evidence/{evidence_id}
DELETE /experiences/{experience_id}/evidence/{evidence_id}
PUT    /experiences/{experience_id}/evidence-order
```

Return the updated expanded experience detail for every successful mutation so the UI can replace local state atomically.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `cd apps/backend && uv run pytest tests/unit/test_experience_repositories.py tests/integration/test_experiences_api.py -q`

Expected: repository and API evidence cases pass, including rollback coverage.

- [ ] **Step 6: Commit Task 4**

```bash
git add apps/backend/app/experience/services/evidence_service.py apps/backend/app/experience/services/experience_service.py apps/backend/app/experience/routers/experiences.py apps/backend/tests/unit/test_experience_repositories.py apps/backend/tests/integration/test_experiences_api.py
git commit -m "feat: manage structured experience evidence"
```

---

### Task 5: Readiness, search, archive, restore, and permanent deletion

**Files:**
- Modify: `apps/backend/app/experience/services/experience_service.py`
- Modify: `apps/backend/app/experience/routers/experiences.py`
- Modify: `apps/backend/tests/integration/test_experiences_api.py`

**Interfaces:**
- Consumes: completeness threshold, repository list/delete behavior, evidence ownership.
- Produces: lifecycle endpoints, deletion-impact contract, active/archive search and sorting.

- [ ] **Step 1: Write failing lifecycle and query tests**

Cover mark-ready below threshold returning `409` with score/missing dimensions; successful ready; destructive edit falling back to draft; active list excluding archived; archive list including archived; restore returning draft; permanent delete rejected before archive; permanent delete removing owned evidence while leaving seeded resume rows unchanged; and search/filter/sort behavior.

- [ ] **Step 2: Run lifecycle tests and verify RED**

Run: `cd apps/backend && uv run pytest tests/integration/test_experiences_api.py -q -k "ready or archive or restore or permanent or search or filter"`

Expected: missing lifecycle routes or incorrect list behavior fails.

- [ ] **Step 3: Implement lifecycle service methods**

Add `mark_ready`, `archive`, `restore`, `deletion_impact`, and `permanently_delete`. Restore always returns to draft. Permanent deletion requires archived status and deletes experience/evidence in one transaction. Current `deletion_impact` returns `affected_matches=[]` and `affected_resumes=[]`; no matching tables or resume rows are touched.

- [ ] **Step 4: Add lifecycle routes and stable impact endpoint**

```http
POST   /experiences/{experience_id}/mark-ready
POST   /experiences/{experience_id}/archive
POST   /experiences/{experience_id}/restore
GET    /experiences/{experience_id}/deletion-impact
DELETE /experiences/{experience_id}/permanent
```

- [ ] **Step 5: Run lifecycle tests and verify GREEN**

Run: `cd apps/backend && uv run pytest tests/integration/test_experiences_api.py tests/integration/test_resume_api.py -q`

Expected: all selected tests pass and resume persistence remains untouched.

- [ ] **Step 6: Commit Task 5**

```bash
git add apps/backend/app/experience/services/experience_service.py apps/backend/app/experience/routers/experiences.py apps/backend/tests/integration/test_experiences_api.py
git commit -m "feat: add experience lifecycle controls"
```

---

### Task 6: Stateless AI questions and typed enrichment patches

**Files:**
- Create: `apps/backend/app/prompts/experience_enrichment.py`
- Create: `apps/backend/app/experience/services/experience_enrichment_service.py`
- Modify: `apps/backend/app/llm.py`
- Modify: `apps/backend/app/experience/schemas/experiences.py`
- Modify: `apps/backend/app/experience/routers/experiences.py`
- Create: `apps/backend/tests/unit/test_experience_enrichment_service.py`
- Modify: `apps/backend/tests/integration/test_experiences_api.py`
- Modify: `apps/backend/tests/unit/test_prompt_guardrails.py`

**Interfaces:**
- Consumes: current persisted experience/evidence, `complete_json(..., schema_type="experience_enrichment")`, configured content language, repository/service transaction boundary.
- Produces: `POST /questions/next`, `POST /answers`, deterministic fallback questions, and a narrow `ExperienceEnrichmentPatch` schema.

- [ ] **Step 1: Write failing service tests for typed patches and truthfulness**

Mock `complete_json` and cover experience-field patch, one-evidence patch, new-evidence creation, invalid ID/status/completeness mutation rejection, unsupported metrics rejection, malformed response rollback, prompt delimiter/injection guardrails, and deterministic fallback when the LLM raises.

```python
async def test_answer_cannot_patch_server_owned_fields(service) -> None:
    mocked_result = {"experience_updates": {"status": "ready", "completeness": 100}}
    with patch("app.experience.services.experience_enrichment_service.complete_json", return_value=mocked_result):
        with pytest.raises(InvalidEnrichmentPatch):
            await service.apply_answer(experience_id=1, question_id="background", answer="More context")
```

- [ ] **Step 2: Run service tests and verify RED**

Run: `cd apps/backend && uv run pytest tests/unit/test_experience_enrichment_service.py -q`

Expected: collection fails because enrichment service/prompt/schema do not exist.

- [ ] **Step 3: Add a dedicated truncation schema type**

Extend `app.llm._appears_truncated` documentation and logic for `schema_type="experience_enrichment"`. Require `question` for question responses or at least one recognized patch operation for answer responses; do not apply resume-array truncation heuristics.

- [ ] **Step 4: Implement prompt and enrichment service**

Prompt requirements: treat raw text/answer as quoted untrusted data, use configured content language, never invent unsupported facts, ask instead of guessing, output JSON only, and only emit permitted patch keys. Service flow: load current state, sanitize and scrub secrets, call LLM, validate typed patch, apply through repositories/services in one transaction, recompute completeness, and return expanded detail plus optional next question. On question-generation LLM failure, return the first deterministic missing-dimension question with `is_fallback=true`; on answer mutation failure, leave state unchanged and return a retryable domain error.

- [ ] **Step 5: Add AI routes**

```http
POST /experiences/{experience_id}/questions/next
POST /experiences/{experience_id}/answers
```

No request or response stores conversation history.

- [ ] **Step 6: Run unit/API tests and verify GREEN**

Run: `cd apps/backend && uv run pytest tests/unit/test_experience_enrichment_service.py tests/unit/test_prompt_guardrails.py tests/integration/test_experiences_api.py -q`

Expected: all selected tests pass, including rollback and fallback cases.

- [ ] **Step 7: Commit Task 6**

```bash
git add apps/backend/app/prompts/experience_enrichment.py apps/backend/app/experience/services/experience_enrichment_service.py apps/backend/app/experience/schemas/experiences.py apps/backend/app/experience/routers/experiences.py apps/backend/app/llm.py apps/backend/tests/unit/test_experience_enrichment_service.py apps/backend/tests/integration/test_experiences_api.py apps/backend/tests/unit/test_prompt_guardrails.py
git commit -m "feat: add AI experience enrichment"
```

---

### Task 7: Frontend API client and import/list workspace

**Files:**
- Create: `apps/frontend/lib/api/experiences.ts`
- Create: `apps/frontend/app/(default)/experiences/page.tsx`
- Create: `apps/frontend/components/experiences/experience-library-page.tsx`
- Create: `apps/frontend/components/experiences/experience-list.tsx`
- Create: `apps/frontend/components/experiences/text-import-dialog.tsx`
- Create: `apps/frontend/tests/api-experiences.test.ts`
- Create: `apps/frontend/tests/experience-library-page.test.tsx`

**Interfaces:**
- Consumes: Task 3–6 API contracts and existing `apiFetch/apiPost/apiPatch/apiPut/apiDelete`.
- Produces: typed frontend API functions, `/experiences` route, active list/search/filter, empty state, and paste-to-persist flow.

- [ ] **Step 1: Write failing API-client contract tests**

Mock `fetch` and assert endpoints, methods, encoded query parameters, JSON bodies, error propagation, and response typing for list/import/create/patch/evidence/lifecycle/question/answer operations.

```typescript
it('imports raw text through the central API path', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(experience)));
  await importExperienceText('Built an agent');
  expect(fetch).toHaveBeenCalledWith(
    '/api/v1/experiences/import-text',
    expect.objectContaining({ method: 'POST', body: JSON.stringify({ text: 'Built an agent' }) })
  );
});
```

- [ ] **Step 2: Run API-client tests and verify RED**

Run: `cd apps/frontend && npm run test -- tests/api-experiences.test.ts`

Expected: module import fails because `lib/api/experiences.ts` does not exist.

- [ ] **Step 3: Implement typed API functions**

Define exact response/request types matching backend snake_case fields. Reuse only the centralized client helpers and check `response.ok` in one local `parseResponse<T>` helper. Export functions for every current endpoint; do not create a new base URL.

- [ ] **Step 4: Run API-client tests and verify GREEN**

Run: `cd apps/frontend && npm run test -- tests/api-experiences.test.ts`

Expected: all API contract tests pass.

- [ ] **Step 5: Write failing page tests for load/search/import**

Mock the API module and verify initial loading, retry error, empty state, query/filter behavior, paste dialog, exact text import, immediate selection of the persisted draft, and preservation of that draft when a later action fails.

- [ ] **Step 6: Run page tests and verify RED**

Run: `cd apps/frontend && npm run test -- tests/experience-library-page.test.tsx`

Expected: route/components are missing.

- [ ] **Step 7: Implement route, list, and import dialog**

Build the Swiss-style toolbar and responsive list/detail shell. On import success, insert/replace the returned record, select it, close the dialog, and render its raw input without starting AI automatically. Include loading, retry, empty, and no-results states. Add `onKeyDown={event => event.key === 'Enter' && event.stopPropagation()}` to textareas.

- [ ] **Step 8: Run page tests and verify GREEN**

Run: `cd apps/frontend && npm run test -- tests/api-experiences.test.ts tests/experience-library-page.test.tsx`

Expected: all selected frontend tests pass.

- [ ] **Step 9: Commit Task 7**

```bash
git add apps/frontend/lib/api/experiences.ts apps/frontend/app/'(default)'/experiences/page.tsx apps/frontend/components/experiences/experience-library-page.tsx apps/frontend/components/experiences/experience-list.tsx apps/frontend/components/experiences/text-import-dialog.tsx apps/frontend/tests/api-experiences.test.ts apps/frontend/tests/experience-library-page.test.tsx
git commit -m "feat: add experience library import workspace"
```

---

### Task 8: Structured editor, evidence, completeness, and recycle bin UI

**Files:**
- Create: `apps/frontend/components/experiences/experience-editor.tsx`
- Create: `apps/frontend/components/experiences/evidence-list-editor.tsx`
- Create: `apps/frontend/components/experiences/completeness-panel.tsx`
- Create: `apps/frontend/components/experiences/permanent-delete-dialog.tsx`
- Modify: `apps/frontend/components/experiences/experience-library-page.tsx`
- Modify: `apps/frontend/tests/experience-library-page.test.tsx`

**Interfaces:**
- Consumes: frontend API module and selected-record state from Task 7.
- Produces: manual metadata editing, evidence card CRUD/reorder, mark-ready, archive/recycle-bin/restore/permanent-delete, and unsaved-change protection.

- [ ] **Step 1: Add failing interaction tests**

Cover metadata save, current/end-date interaction, add/edit/delete/reorder evidence as connected cards, server-returned completeness refresh, ready conflict display, archive disappearance from active list, recycle-bin restore, impact lookup before permanent-delete confirmation, and dirty selection/navigation confirmation.

- [ ] **Step 2: Run interaction tests and verify RED**

Run: `cd apps/frontend && npm run test -- tests/experience-library-page.test.tsx`

Expected: editor/lifecycle controls are not present.

- [ ] **Step 3: Implement structured editor and evidence cards**

Use controlled drafts; save only explicit editable fields. Evidence cards keep action/result/metrics together. Replace the selected server record after every mutation so completeness and derived guidance stay authoritative. Disable double submission and retain local edits on failures.

- [ ] **Step 4: Implement lifecycle and recycle-bin UI**

Provide active/archive view switching. Archive is the normal delete action. Restore returns the record to active draft. Permanent delete exists only in archive view, calls deletion-impact before rendering confirmation, lists affected matches when later available, and removes the item locally only after successful server deletion.

- [ ] **Step 5: Implement unsaved-change guard**

Before selecting another record or leaving through in-app navigation, compare normalized draft and selected data. Use the existing confirmation-dialog style. Add `beforeunload` only while dirty and remove it on cleanup.

- [ ] **Step 6: Run interaction tests and verify GREEN**

Run: `cd apps/frontend && npm run test -- tests/experience-library-page.test.tsx tests/api-experiences.test.ts`

Expected: all selected frontend tests pass.

- [ ] **Step 7: Commit Task 8**

```bash
git add apps/frontend/components/experiences apps/frontend/tests/experience-library-page.test.tsx
git commit -m "feat: add experience editing and lifecycle UI"
```

---

### Task 9: AI question panel, dashboard entry, and six-locale copy

**Files:**
- Create: `apps/frontend/components/experiences/experience-question-panel.tsx`
- Modify: `apps/frontend/components/experiences/experience-library-page.tsx`
- Modify: `apps/frontend/app/(default)/dashboard/page.tsx`
- Modify: `apps/frontend/messages/en.json`
- Modify: `apps/frontend/messages/es.json`
- Modify: `apps/frontend/messages/fr.json`
- Modify: `apps/frontend/messages/ja.json`
- Modify: `apps/frontend/messages/pt-BR.json`
- Modify: `apps/frontend/messages/zh.json`
- Modify: `apps/frontend/tests/experience-library-page.test.tsx`
- Create: `apps/frontend/tests/dashboard-experience-library.test.tsx`
- Modify: `apps/frontend/tests/i18n-locale-parity.test.ts`

**Interfaces:**
- Consumes: next-question/answer API, page selected state, existing translations/dashboard grid.
- Produces: one-question-at-a-time enrichment panel, deterministic fallback indicator, dashboard link, and complete localized static chrome.

- [ ] **Step 1: Write failing AI-panel and dashboard tests**

Cover explicit `Help me organize with AI` start, one question, answer submission, updated detail replacement, next question, loading/error/retry, manual fallback, fallback-question badge, no local conversation persistence, and dashboard link to `/experiences`.

- [ ] **Step 2: Run tests and verify RED**

Run: `cd apps/frontend && npm run test -- tests/experience-library-page.test.tsx tests/dashboard-experience-library.test.tsx`

Expected: AI panel and dashboard entry are absent.

- [ ] **Step 3: Implement the question panel**

The component holds only the current transient question/answer. It requests the next question on explicit user action, submits one answer, replaces the selected detail from the response, and discards the answered text. It never writes chat history to localStorage or backend storage. AI failure keeps the persisted experience visible and exposes retry plus manual editing.

- [ ] **Step 4: Add dashboard entry without changing unrelated dashboard logic**

Add one Swiss-grid card linking to `/experiences`, with its own icon and localized title/description. Adjust filler count based on the extra real card. Preserve any pre-existing user modifications in `dashboard/page.tsx` by editing only the relevant import/count/render sections.

- [ ] **Step 5: Add identical `experienceLibrary.*` keys to all six locale files**

Include page headings, kinds/statuses, fields, import/editor/evidence/completeness/AI/lifecycle/errors/actions, and dashboard copy. Use natural translations rather than copying English into every locale.

- [ ] **Step 6: Run UI and locale tests and verify GREEN**

Run: `cd apps/frontend && npm run test -- tests/experience-library-page.test.tsx tests/dashboard-experience-library.test.tsx tests/i18n-locale-parity.test.ts`

Expected: all selected tests pass and locale structures are identical.

- [ ] **Step 7: Commit Task 9**

```bash
git add apps/frontend/components/experiences/experience-question-panel.tsx apps/frontend/components/experiences/experience-library-page.tsx apps/frontend/app/'(default)'/dashboard/page.tsx apps/frontend/messages apps/frontend/tests/experience-library-page.test.tsx apps/frontend/tests/dashboard-experience-library.test.tsx apps/frontend/tests/i18n-locale-parity.test.ts
git commit -m "feat: complete experience library workflow"
```

---

### Task 10: Full verification, documentation alignment, and completion audit

**Files:**
- Modify only if commands or behavior differ: `docs/agent/apis/front-end-apis.md`
- Modify only if user-facing launch instructions require it: `README.zh-CN.md`
- Verify: `docs/superpowers/specs/2026-07-29-personal-experience-library-design.md`
- Verify: `docs/superpowers/specs/2026-07-29-personal-experience-library-design.zh-CN.md`

**Interfaces:**
- Consumes: all implemented tasks and their test evidence.
- Produces: a verified, reviewable branch satisfying every current-phase requirement; future matching remains an explicit non-goal with stable deletion-impact contract.

- [ ] **Step 1: Run the complete backend deterministic suite**

Run: `cd apps/backend && uv run pytest`

Expected: exit 0 with no failures; eval-marked real-LLM tests remain excluded by project configuration.

- [ ] **Step 2: Run the complete frontend test suite**

Run: `cd apps/frontend && npm run test`

Expected: exit 0 with no failed tests.

- [ ] **Step 3: Run backend formatting/static syntax checks used by the repository**

Run: `cd apps/backend && uv run python -m compileall -q app tests`

Expected: exit 0.

- [ ] **Step 4: Run frontend lint and production build**

Run: `cd apps/frontend && npm run lint`

Expected: exit 0 with no lint errors.

Run: `cd apps/frontend && npm run build`

Expected: exit 0 and Next.js reports a successful production build including `/experiences`.

- [ ] **Step 5: Perform a requirement-by-requirement audit**

Create a temporary checklist from spec sections 2, 5–13, and 15. For each requirement, record the proving model/schema/repository/service/router/component plus a covering test or command. Any missing or indirect evidence is unfinished work and must be fixed before completion.

- [ ] **Step 6: Review the complete branch**

Use `superpowers:requesting-code-review` with the merge-base-to-HEAD review package. Resolve every Critical/Important issue, rerun affected tests, then perform one scoped re-review.

- [ ] **Step 7: Commit documentation corrections, if any**

```bash
git add docs/agent/apis/front-end-apis.md README.zh-CN.md
git commit -m "docs: document experience library APIs"
```

Skip this commit when neither file needed changes.
