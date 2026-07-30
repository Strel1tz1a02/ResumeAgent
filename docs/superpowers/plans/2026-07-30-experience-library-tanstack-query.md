# Personal Experience Library TanStack Query Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every Personal Experience Library server-state request and hand-written request-race guard with a route-scoped TanStack Query v5 layer while preserving the existing product behavior and backend optimistic-concurrency contract.

**Architecture:** A feature-owned query package defines canonical keys, cancellable read hooks, scoped mutation hooks, and immutable cache helpers. `ExperienceLibraryPage` owns a route-lifetime provider and local UI state only; child components consume mutations while form drafts remain local. Reads use TanStack `AbortSignal`; writes are serialized per experience and update caches only from authoritative server responses.

**Tech Stack:** Next.js 16, React 19, TypeScript 5, `@tanstack/react-query` v5, Vitest 4, Testing Library.

## Global Constraints

- Change only the Personal Experience Library module, plus the central API client's signal-composition support required by that module.
- Preserve all backend routes, schemas, database behavior, AI provenance checks, completeness rules, deletion semantics, and `expected_updated_at`/409 handling.
- Keep form drafts, dirty guards, filters, selection, dialogs, and transient AI answer text as local UI state.
- Do not add Query Devtools, cache persistence, global providers, optimistic factual writes, or automatic mutation retries.
- Every production change follows red-green-refactor: add a failing test, verify the expected failure, implement the minimum behavior, then rerun the focused test.
- Work directly in the current checkout; do not create a worktree.

---

## File Map

**Create**

- `apps/frontend/lib/queries/experiences/provider.tsx`: stable route-scoped `QueryClientProvider`.
- `apps/frontend/lib/queries/experiences/keys.ts`: canonical query key factory and status type.
- `apps/frontend/lib/queries/experiences/cache.ts`: authoritative immutable list/detail cache synchronization.
- `apps/frontend/lib/queries/experiences/queries.ts`: list, detail, and deletion-impact hooks.
- `apps/frontend/lib/queries/experiences/mutations.ts`: all experience-library mutation hooks and shared mutation scopes.
- `apps/frontend/tests/experience-query-cache.test.tsx`: query keys, cache synchronization, cancellation, and mutation serialization tests.

**Modify**

- `apps/frontend/package.json` and `apps/frontend/package-lock.json`: add TanStack Query v5.
- `apps/frontend/lib/api/client.ts`: combine an external abort signal with the existing timeout.
- `apps/frontend/lib/api/experiences.ts`: accept signals on list/detail/impact reads.
- `apps/frontend/components/experiences/experience-library-page.tsx`: provider, queries, creation, refresh, ready, archive, and restore.
- `apps/frontend/components/experiences/experience-editor.tsx`: metadata mutation and local-draft response guard.
- `apps/frontend/components/experiences/evidence-list-editor.tsx`: evidence mutations.
- `apps/frontend/components/experiences/experience-question-panel.tsx`: AI mutations.
- `apps/frontend/components/experiences/text-import-dialog.tsx`: import mutation.
- `apps/frontend/components/experiences/permanent-delete-dialog.tsx`: impact query and permanent-delete mutation.
- `apps/frontend/tests/api-client.test.ts`: external abort and timeout distinction.
- `apps/frontend/tests/api-experiences.test.ts`: read signal forwarding.
- `apps/frontend/tests/experience-library-page.test.tsx`: provider-aware behavior and race regressions.
- `docs/agent/apis/front-end-apis.md`: cache ownership and cancellation contract.

---

### Task 1: Install TanStack Query and add the route-scoped provider

**Files:**

- Modify: `apps/frontend/package.json`
- Modify: `apps/frontend/package-lock.json`
- Create: `apps/frontend/lib/queries/experiences/provider.tsx`
- Modify: `apps/frontend/components/experiences/experience-library-page.tsx`
- Test: `apps/frontend/tests/experience-library-page.test.tsx`

**Interfaces:**

- Produces: `createExperienceQueryClient(): QueryClient`
- Produces: `ExperienceQueryProvider({ children }: PropsWithChildren): ReactNode`
- Produces: route-scoped defaults with query/mutation retry disabled and focus/reconnect refetch disabled.

- [ ] **Step 1: Install the production dependency**

Run in `apps/frontend`:

```powershell
npm.cmd install @tanstack/react-query@^5
```

Expected: `package.json` and lockfile contain `@tanstack/react-query` v5.

- [ ] **Step 2: Write the failing provider test**

Add a test that renders `ExperienceLibraryPage` and asserts it no longer throws the TanStack error `No QueryClient set`. Add a direct default-options assertion:

```tsx
const client = createExperienceQueryClient();
expect(client.getDefaultOptions().queries).toMatchObject({
  retry: false,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
});
expect(client.getDefaultOptions().mutations).toMatchObject({ retry: false });
```

- [ ] **Step 3: Run the test and verify RED**

Run:

```powershell
npx.cmd vitest run tests/experience-library-page.test.tsx -t "provides a route-scoped query client"
```

Expected: FAIL because `createExperienceQueryClient` and the provider do not exist.

- [ ] **Step 4: Implement the provider and wrap the route component**

Create the provider with a client created once per provider instance:

```tsx
'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, type PropsWithChildren } from 'react';

export function createExperienceQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
      },
      mutations: { retry: false },
    },
  });
}

export function ExperienceQueryProvider({ children }: PropsWithChildren) {
  const [client] = useState(createExperienceQueryClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
```

Rename the existing implementation to an internal `ExperienceLibraryContent` and make the exported page return it under `ExperienceQueryProvider`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```powershell
npx.cmd vitest run tests/experience-library-page.test.tsx
```

Expected: existing page tests plus the provider test pass.

- [ ] **Step 6: Commit**

```powershell
git add apps/frontend/package.json apps/frontend/package-lock.json apps/frontend/lib/queries/experiences/provider.tsx apps/frontend/components/experiences/experience-library-page.tsx apps/frontend/tests/experience-library-page.test.tsx
git commit -m "feat: add experience query provider"
```

---

### Task 2: Make experience reads cancellable through the API client

**Files:**

- Modify: `apps/frontend/lib/api/client.ts`
- Modify: `apps/frontend/lib/api/experiences.ts`
- Modify: `apps/frontend/tests/api-client.test.ts`
- Modify: `apps/frontend/tests/api-experiences.test.ts`

**Interfaces:**

- Changes: `apiFetch(endpoint, options?, timeoutMs?)` respects `options.signal` while retaining timeout cancellation.
- Changes: `listExperiences(query?, signal?)`, `fetchExperience(id, signal?)`, and `getDeletionImpact(id, signal?)` forward the signal.
- Guarantees: an external abort remains an abort/cancellation, while an internal timeout receives the existing friendly timeout message.

- [ ] **Step 1: Write failing external-abort tests**

Add an API client test that captures the signal passed to `fetch`, aborts an external controller, and expects the captured signal to abort without receiving the timeout message. Add transport tests such as:

```ts
const controller = new AbortController();
await listExperiences({ status: 'active' }, controller.signal);
expect(apiFetch).toHaveBeenCalledWith('/experiences?status=active', {
  signal: controller.signal,
});
```

Use the actual mocked call shape implemented by the module rather than weakening the assertion with an unbounded matcher.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
npx.cmd vitest run tests/api-client.test.ts tests/api-experiences.test.ts
```

Expected: FAIL because the experience functions do not accept a signal and `apiFetch` overwrites an external signal.

- [ ] **Step 3: Implement composed cancellation**

In `apiFetch`, create the timeout controller, mirror an external signal into it, track whether the timeout fired, and remove the listener in `finally`. Only translate `AbortError` to the friendly timeout error when the timeout fired. Update the three read functions to pass `{ signal }` to `apiFetch` when provided.

Required behavior:

```ts
let timedOut = false;
const abortFromCaller = () => controller.abort(options?.signal?.reason);
if (options?.signal?.aborted) abortFromCaller();
else options?.signal?.addEventListener('abort', abortFromCaller, { once: true });
const timer = setTimeout(() => {
  timedOut = true;
  controller.abort();
}, timeout);
```

The final `fetch` options must use `controller.signal`, not the external signal directly.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
npx.cmd vitest run tests/api-client.test.ts tests/api-experiences.test.ts
```

Expected: both suites pass, including the existing timeout tests.

- [ ] **Step 5: Commit**

```powershell
git add apps/frontend/lib/api/client.ts apps/frontend/lib/api/experiences.ts apps/frontend/tests/api-client.test.ts apps/frontend/tests/api-experiences.test.ts
git commit -m "feat: support cancellable experience reads"
```

---

### Task 3: Add canonical query keys, read hooks, and cache helpers

**Files:**

- Create: `apps/frontend/lib/queries/experiences/keys.ts`
- Create: `apps/frontend/lib/queries/experiences/cache.ts`
- Create: `apps/frontend/lib/queries/experiences/queries.ts`
- Create: `apps/frontend/tests/experience-query-cache.test.tsx`

**Interfaces:**

- Produces: `experienceKeys` factory from the approved design.
- Produces: `writeExperienceDetail(client, detail): void`.
- Produces: `removeExperienceFromCache(client, experienceId): void`.
- Produces: `useExperienceList(view)`, `useExperienceDetail(id)`, and `useDeletionImpact(id, enabled)`.

- [ ] **Step 1: Write failing key and cache tests**

Test exact keys and seed both list caches plus a detail cache. Apply a ready detail and assert it is present in active, absent from archived, and stored under its detail key. Apply an archived detail and assert the reverse. Remove an ID and assert list, detail, and deletion-impact entries are gone.

Also render `useExperienceDetail(1)`, switch to ID 2 before ID 1 resolves, and assert the signal passed to the first mocked transport is aborted and only detail 2 is observable.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
npx.cmd vitest run tests/experience-query-cache.test.tsx
```

Expected: FAIL because the query package does not exist.

- [ ] **Step 3: Implement keys and immutable cache helpers**

Use tuple-returning key functions and `setQueriesData<ExperienceListResponse>` to update every cached list without mutating existing arrays. Treat `status === 'archived'` as archived; `draft` and `ready` belong to active.

`writeExperienceDetail` must reject an older response for the same detail cache by comparing ISO `updated_at` values before replacement. List entries use the same comparison so a late response cannot roll back a newer cached item.

- [ ] **Step 4: Implement read hooks**

Each query function consumes the context signal:

```ts
export function useExperienceDetail(experienceId: number | null) {
  return useQuery({
    queryKey: experienceKeys.detail(experienceId as number),
    queryFn: ({ signal }) => fetchExperience(experienceId as number, signal),
    enabled: experienceId !== null,
  });
}
```

Use a non-colliding disabled key when the ID is null if needed to satisfy TypeScript without casting a null ID into a live query.

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```powershell
npx.cmd vitest run tests/experience-query-cache.test.tsx
```

Expected: key, cache, and cancellation tests pass.

- [ ] **Step 6: Commit**

```powershell
git add apps/frontend/lib/queries/experiences apps/frontend/tests/experience-query-cache.test.tsx
git commit -m "feat: add experience query cache"
```

---

### Task 4: Add all feature mutation hooks and prove serialization

**Files:**

- Create: `apps/frontend/lib/queries/experiences/mutations.ts`
- Modify: `apps/frontend/tests/experience-query-cache.test.tsx`

**Interfaces:**

- Produces: `EXPERIENCE_CREATION_SCOPE`.
- Produces: `experienceMutationScope(id): { id: string }`.
- Produces hooks for manual create, import, metadata patch, evidence CRUD/reorder, AI question/answer, ready/archive/restore, and permanent deletion.
- Every detail-returning mutation calls `writeExperienceDetail` in its hook-level `onSuccess`.
- Permanent deletion calls `removeExperienceFromCache` in its hook-level `onSuccess`.

- [ ] **Step 1: Write failing mutation tests**

Render two mutation hooks with the same experience ID, start deferred metadata and evidence mutations, and assert only the first transport begins until it resolves. Render mutations for two IDs and assert both begin immediately. Assert a successful detail response updates detail/list caches and does not trigger `fetchExperience`.

Test manual create/import share the creation scope and expose a shared pending count through `useIsMutating({ mutationKey: experienceMutationKeys.creation() })` or an exported `useExperienceCreationPending` hook.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
npx.cmd vitest run tests/experience-query-cache.test.tsx
```

Expected: FAIL because mutation hooks and scopes do not exist.

- [ ] **Step 3: Implement mutation keys, scopes, and authoritative success handlers**

Every existing-experience mutation must include:

```ts
scope: experienceMutationScope(experienceId),
retry: false,
onSuccess: (detail) => writeExperienceDetail(queryClient, detail),
```

AI next-question is read-like but remains a mutation because the route uses POST and may invoke an LLM. It does not update the experience cache. AI answer updates the cache with the returned extended detail and returns `next_question` to the panel.

Creation/import hooks both use `mutationKey: experienceMutationKeys.creation()` and `scope: { id: EXPERIENCE_CREATION_SCOPE }`. Permanent deletion removes caches after success. Do not add optimistic `onMutate` data writes.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
npx.cmd vitest run tests/experience-query-cache.test.tsx
```

Expected: mutation serialization, isolation, and cache-update tests pass.

- [ ] **Step 5: Commit**

```powershell
git add apps/frontend/lib/queries/experiences/mutations.ts apps/frontend/tests/experience-query-cache.test.tsx
git commit -m "feat: add scoped experience mutations"
```

---

### Task 5: Migrate the library page, creation/import, and lifecycle flows

**Files:**

- Modify: `apps/frontend/components/experiences/experience-library-page.tsx`
- Modify: `apps/frontend/components/experiences/text-import-dialog.tsx`
- Modify: `apps/frontend/components/experiences/permanent-delete-dialog.tsx`
- Modify: `apps/frontend/tests/experience-library-page.test.tsx`

**Interfaces:**

- Consumes: provider, list/detail/impact queries, creation/import and lifecycle mutations.
- Removes: list/detail request-generation refs, request-only mounted ref, duplicated list/detail loading state, direct experience transport imports.
- Preserves: dirty navigation, selected view/item, local filters, localized errors, mobile panes, and all lifecycle confirmation behavior.

- [ ] **Step 1: Add failing page race and cache tests**

Retain existing deferred-request regressions and change their expected mechanism to TanStack behavior. Add assertions that:

- an older active-list response cannot replace the archived view;
- importing while an old list request is pending selects the imported detail;
- closing and reopening permanent-delete for a different ID never displays the first ID's impact;
- manual refresh calls query refetch and keeps the current selection when present;
- both New Experience and Import are disabled while the shared creation scope is pending.

- [ ] **Step 2: Run page tests and verify RED**

Run:

```powershell
npx.cmd vitest run tests/experience-library-page.test.tsx
```

Expected: new TanStack-specific assertions fail while existing behavior remains the reference.

- [ ] **Step 3: Migrate list/detail state and refresh**

Replace the list/detail effects with `useExperienceList(view)` and `useExperienceDetail(selectedExperienceId)`. Derive loading/error from query results. On list changes, preserve the current selection when it remains present; otherwise reuse the existing dirty-discard gate before selecting the first item.

Manual refresh must call `refetch` for the current list and selected detail rather than calling transports. Selection changes only change the ID; query keys isolate responses.

- [ ] **Step 4: Migrate create/import and lifecycle operations**

Use hook `mutateAsync` calls. On successful create/import, select the returned ID and switch to active/detail view; the hook has already populated caches. Use mutation pending/error state for ready/archive/restore. The impact dialog consumes its query and delete mutation directly.

Remove `onMutationStart`, `onMutated`, and `onDeleted` props whose only purpose was request invalidation or parent cache synchronization. Keep callbacks that close dialogs or change selection after confirmed success.

- [ ] **Step 5: Run page tests and verify GREEN**

Run:

```powershell
npx.cmd vitest run tests/experience-library-page.test.tsx
```

Expected: all page tests pass with no hand-written request generations.

- [ ] **Step 6: Commit**

```powershell
git add apps/frontend/components/experiences/experience-library-page.tsx apps/frontend/components/experiences/text-import-dialog.tsx apps/frontend/components/experiences/permanent-delete-dialog.tsx apps/frontend/tests/experience-library-page.test.tsx
git commit -m "refactor: move experience page state to queries"
```

---

### Task 6: Migrate metadata, evidence, and AI child components

**Files:**

- Modify: `apps/frontend/components/experiences/experience-editor.tsx`
- Modify: `apps/frontend/components/experiences/evidence-list-editor.tsx`
- Modify: `apps/frontend/components/experiences/experience-question-panel.tsx`
- Modify: `apps/frontend/components/experiences/experience-library-page.tsx`
- Modify: `apps/frontend/tests/experience-library-page.test.tsx`

**Interfaces:**

- Consumes: patch, evidence, and enrichment mutation hooks.
- Removes: direct API imports, `saveGenerationRef`, AI `generationRef`, AI request-only mounted ref, manual pending flags duplicated by mutations, and parent cache-update callbacks.
- Preserves: draft merge/reset rules, dirty flags, retry actions, Enter propagation rules, and the keyed AI session.

- [ ] **Step 1: Add failing child-operation tests**

Add or adapt tests proving:

- saving A and selecting B before A resolves never changes B's draft;
- editing the same draft again while its save is pending prevents the first success from clearing the newer text;
- evidence success updates the displayed completeness/evidence from cache while preserving a separate dirty metadata draft;
- AI answer success updates the authoritative detail but does not clear a dirty metadata draft;
- AI retry uses the correct question/answer mutation after failure;
- the metadata payload retains the cached detail's `expected_updated_at`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
npx.cmd vitest run tests/experience-library-page.test.tsx -t "save|evidence|AI"
```

Expected: at least the new mutation/cache assertions fail before migration.

- [ ] **Step 3: Migrate metadata and evidence editors**

Metadata editor calls the patch mutation and records a normalized submitted-draft snapshot. On success, reset the local form only when the component still represents the submitted experience and the current normalized draft equals that submitted snapshot. The hook performs cache synchronization.

Evidence editor selects the relevant mutation for each button. Its operation key may remain local for button labels, but request pending and error values come from mutations. Preserve the baseline merge that protects dirty evidence rows when authoritative cache data changes.

- [ ] **Step 4: Migrate AI enrichment**

Use separate question and answer mutations. Derive `pending` and errors from them. Keep question, answer, start, and retry intent local. Because the component is keyed by experience ID, selection changes unmount its transient session. Hook-level success writes answer responses to cache even if the component unmounts; component-level continuation updates the next question only for the mounted session.

- [ ] **Step 5: Prove no experience component directly imports transports**

Run:

```powershell
rg -n "from '@/lib/api/experiences'" apps/frontend/components/experiences
```

Expected: type-only imports are permitted only if moving shared types would add unnecessary indirection; no imported API function names or direct calls remain.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run:

```powershell
npx.cmd vitest run tests/experience-library-page.test.tsx tests/experience-query-cache.test.tsx
```

Expected: both suites pass.

- [ ] **Step 7: Commit**

```powershell
git add apps/frontend/components/experiences/experience-editor.tsx apps/frontend/components/experiences/evidence-list-editor.tsx apps/frontend/components/experiences/experience-question-panel.tsx apps/frontend/components/experiences/experience-library-page.tsx apps/frontend/tests/experience-library-page.test.tsx
git commit -m "refactor: migrate experience mutations to TanStack"
```

---

### Task 7: Documentation, architecture audit, and full verification

**Files:**

- Modify: `docs/agent/apis/front-end-apis.md`
- Modify: `docs/superpowers/plans/2026-07-30-experience-library-tanstack-query.md`
- Test: all frontend and backend suites.

**Interfaces:**

- Documents: query keys, cache ownership, cancellation rules, mutation serialization, and preserved backend 409 contract.
- Produces: a clean, tested commit with no request-generation refs in the experience module.

- [ ] **Step 1: Update documentation**

Document that the Personal Experience Library alone uses a route-scoped TanStack Query v5 client; list/read/impact requests consume cancellation signals; same-experience mutations serialize; authoritative mutation responses update cache; and backend optimistic concurrency remains mandatory.

- [ ] **Step 2: Run architecture searches**

Run:

```powershell
rg -n "RequestGeneration|requestGeneration|generationRef|isMountedRef|mountedRef" apps/frontend/components/experiences apps/frontend/lib/queries/experiences
rg -n "listExperiences|fetchExperience|patchExperience|createEvidence|submitExperienceAnswer|getDeletionImpact" apps/frontend/components/experiences
```

Expected: no request-race generation/mounted guards and no direct transport calls remain. A local non-request ref is acceptable only when its name and purpose clearly concern form baseline or dirty navigation.

- [ ] **Step 3: Run the complete frontend verification**

Run in `apps/frontend`:

```powershell
npm.cmd test -- --run
npm.cmd run lint
npx.cmd tsc --noEmit
npm.cmd run build
```

Expected: every command exits 0 and the production route table includes `/experiences`.

- [ ] **Step 4: Run backend regression verification**

Run in `apps/backend`:

```powershell
E:\anaconda\envs\resume-matcher\python.exe -m pytest -q
E:\anaconda\envs\resume-matcher\python.exe -m compileall -q app tests
```

Expected: the full backend suite passes with only the already-known Windows POSIX skip and third-party LiteLLM warnings.

- [ ] **Step 5: Check the final diff and status**

Run:

```powershell
git diff --check
git status --short
```

Expected: `git diff --check` exits 0; status contains only intended migration files before commit.

- [ ] **Step 6: Commit final documentation and verification adjustments**

```powershell
git add docs/agent/apis/front-end-apis.md docs/superpowers/plans/2026-07-30-experience-library-tanstack-query.md
git commit -m "docs: verify experience query migration"
```

---

## Plan Self-review

- Every requirement in `docs/superpowers/specs/2026-07-30-experience-library-tanstack-query-design.md` maps to Tasks 1–7.
- The provider and dependency are feature-scoped; no other route is migrated.
- Query keys and cache-helper signatures are consistent across read, mutation, and component tasks.
- Read cancellation and write serialization are deliberately separate.
- The plan contains no migration of form drafts into TanStack Query and no optimistic factual writes.
- Each behavior change has an explicit failing-test step before production implementation.
