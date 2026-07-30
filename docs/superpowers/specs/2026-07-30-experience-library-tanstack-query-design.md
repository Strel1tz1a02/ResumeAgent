# Personal Experience Library TanStack Query Design

**Date:** 2026-07-30

**Status:** Design approved; awaiting written review

**Scope:** Replace hand-written asynchronous request and race management only in the Personal Experience Library frontend. Preserve the existing product, API, database, and backend concurrency design.

## 1. Objective

The Personal Experience Library currently coordinates list requests, detail requests, metadata saves, evidence mutations, AI enrichment, and lifecycle mutations with component-local loading flags and request-generation refs. This works, but duplicates server-state machinery and makes cross-operation race behavior difficult to reason about.

The module will use TanStack Query v5 as its single server-state layer. Components will retain only local UI state: form drafts, dirty flags, filters, selected view and item, mobile-pane state, and dialogs. The backend `expected_updated_at` token and HTTP 409 response remain the final protection against stale writes.

## 2. Scope and Non-goals

### In scope

- Active and archived experience lists.
- Selected experience detail.
- Manual creation and text import.
- Metadata save.
- Evidence create, update, delete, and reorder.
- AI next-question and answer submission requests.
- Mark-ready, archive, restore, deletion-impact, and permanent-delete operations.
- Query cancellation, mutation serialization, cache synchronization, error exposure, and pending state for those operations.
- Tests and API-client support required by the migration.

### Out of scope

- Migrating any Resume Matcher module other than the Personal Experience Library.
- Changing backend routes, schemas, persistence, completeness, AI provenance, or deletion semantics.
- Persisting AI conversation state or answers in browser storage.
- Replacing local form state with TanStack Query.
- Optimistic writes that display unconfirmed experience facts.
- Global navigation or application-wide server-state architecture changes.

## 3. Chosen Architecture

### 3.1 Route-scoped provider

The `/experiences` route will be wrapped by a client-side `ExperienceQueryProvider`. It owns one stable `QueryClient` instance for the route lifetime. No other route is placed under this provider.

Provider defaults:

- Query retries are disabled for normal API errors because the existing client exposes user-actionable failures and several calls may invoke an LLM.
- `refetchOnWindowFocus` is false so returning to the window cannot refresh the editing baseline unexpectedly.
- `refetchOnReconnect` is false; the user can retry or refresh explicitly.
- Query garbage collection uses the TanStack default unless a focused test demonstrates a need to change it.
- Mutation retries are disabled. Retrying imports, AI answers, or destructive operations without an idempotency contract is unsafe.

### 3.2 Feature-owned query layer

TanStack-specific code will live under `apps/frontend/lib/queries/experiences/`, separate from transport functions in `lib/api/experiences.ts`.

Responsibilities:

- `keys.ts`: canonical query-key factory.
- `cache.ts`: immutable helpers that write one authoritative `ExperienceDetail` into detail and list caches, and remove a permanently deleted item.
- `queries.ts`: list, detail, and deletion-impact query options/hooks.
- `mutations.ts`: creation, import, metadata, evidence, AI, and lifecycle mutation hooks.
- `provider.tsx`: route-scoped `QueryClientProvider` with stable defaults.

Components consume hooks and do not call experience API functions directly.

## 4. Query Keys and Fetch Policy

Canonical keys:

```ts
experienceKeys.all                         // ['experiences']
experienceKeys.lists()                     // ['experiences', 'list']
experienceKeys.list('active')              // ['experiences', 'list', 'active']
experienceKeys.list('archived')            // ['experiences', 'list', 'archived']
experienceKeys.details()                   // ['experiences', 'detail']
experienceKeys.detail(experienceId)        // ['experiences', 'detail', experienceId]
experienceKeys.deletionImpact(experienceId)// ['experiences', 'deletion-impact', experienceId]
```

List queries are keyed only by server-side status. Search and kind filters remain client-side and operate on the cached list, preserving the existing behavior.

Detail queries are enabled only when an experience ID is selected. Their query functions consume the `AbortSignal` supplied by TanStack Query and pass it through the API client. When selection changes or a query is explicitly cancelled, an obsolete response cannot replace the selected detail.

Deletion impact is enabled only while the permanent-delete dialog is open for an archived experience. Closing or retargeting the dialog cancels or detaches the obsolete query.

## 5. API Client Cancellation

The central request client will accept an optional external `AbortSignal`. It will combine that signal with the existing request-timeout abort behavior without removing the timeout.

Experience list, detail, and deletion-impact transport functions accept an optional signal and pass it to the central client. Mutation requests are not treated as cancellable server operations: cancelling a browser promise cannot guarantee that a server write did not commit.

This distinction is required:

- Reads use transport cancellation to prevent obsolete work and cache updates.
- Writes use mutation identity, serial scopes, authoritative responses, and backend optimistic concurrency.

## 6. Mutation Rules

### 6.1 Serialization

All mutations that modify an existing experience use the same TanStack mutation scope ID:

```ts
scope: { id: `experience:${experienceId}` }
```

This serializes metadata, evidence, AI answer, ready, archive, restore, and permanent-delete writes for the same experience. Mutations for different experiences may proceed independently.

Creation and import use one library creation scope so requests from either entry point cannot execute concurrently. The route observes that shared pending scope and disables both creation entry points until it is idle; this prevents a queued duplicate caused by clicking the other entry point while one creation request is running.

### 6.2 Cache updates

When a mutation returns an `ExperienceDetail`, the query layer synchronously and immutably:

1. Replaces `detail(experienceId)` with the authoritative response.
2. Upserts the item in the list matching its returned status.
3. Removes the item from the opposite-status list.
4. Removes any cached deletion impact made obsolete by a restore or permanent delete.

No response is allowed to update the visible editor for a different query key. A late mutation for experience A may update A's cache entry, but the UI observing `detail(B)` remains unchanged.

Permanent deletion removes the detail, deletion-impact, and list entries only after server success. Import and manual creation insert the returned draft into the active list and detail cache before selecting it.

### 6.3 No speculative factual updates

The module will not optimistically apply edited experience fields, AI-derived facts, or destructive lifecycle changes before server confirmation. This preserves truthful UI behavior and avoids complex rollback paths. Pending indicators provide immediate feedback.

## 7. Component Behavior

### Experience library page

- Reads list and selected detail from queries.
- Keeps selection, filters, view, dirty guards, dialogs, and mobile-pane state locally.
- Uses mutations for manual create, import, ready, archive, and restore.
- Manual refresh calls query invalidation/refetch rather than invoking the transport directly.
- Removes list/detail generation refs, mounted refs used only for requests, and manual loading state that duplicates query state.

### Metadata editor

- Keeps the form draft and dirty calculation locally.
- Saves through a scoped metadata mutation.
- Sends the cached detail's `updated_at` as `expected_updated_at`.
- A mutation success updates the cache; the editor resets its draft only when it still represents the same experience and the submitted draft has not been superseded locally.
- HTTP 409 remains visible as a save error and never overwrites the newer cached/server state.

### Evidence editor

- Keeps each editable evidence draft locally.
- Uses scoped mutations for create, patch, delete, and reorder.
- Applies returned authoritative detail through the shared cache helper.
- Existing dirty-navigation protections remain unchanged.

### AI enrichment panel

- Uses mutations for next-question and answer calls.
- Keeps only the current transient question, answer text, start state, and retry intent locally; no conversation history is persisted.
- Mutation state replaces manual pending/error flags and generation refs.
- The panel is keyed by experience ID, so switching selection isolates transient AI state.
- Successful answers update the shared experience cache and then expose the returned next question.
- If local metadata/evidence becomes dirty before a response is consumed, the response may update the authoritative cache but must not clear or replace the local draft. A later stale metadata save is rejected by `expected_updated_at`.

### Permanent-delete dialog

- Loads impact through an enabled query keyed by experience ID.
- Deletes through a scoped mutation.
- A stale impact response for a previously opened experience cannot appear for the current target because it belongs to a different key.

## 8. Error Handling

- Query errors render through the existing localized module error surfaces.
- Mutation errors remain attached to the operation that failed; switching to another experience cannot display the previous experience's error.
- AI question and answer failures preserve explicit retry behavior.
- A 409 metadata conflict is not automatically retried.
- Permanent deletion remains disabled until deletion-impact data for the current experience key is successfully available.
- Abort/cancellation is not shown as a user-facing failure.

## 9. Testing Strategy

Tests use a fresh `QueryClient` per render with retries disabled. Existing API transport mocks remain the network boundary.

Required regression coverage:

- Provider creates a stable client and scopes it to the experience route.
- Switching A to B while A's detail request is pending never renders A as B.
- Switching active/archived views while an earlier list request is pending keeps the current view's list.
- A late save response for A updates only A's cache and never B's editor draft.
- Two writes for the same experience are serialized; writes for different experiences are not globally blocked.
- An authoritative mutation response updates detail and the correct list without an unnecessary detail refetch.
- Manual creation and text import select the returned persisted draft despite an older list request.
- Evidence mutations and AI answers update the same detail cache consistently.
- Dirty drafts survive cache updates and still send their original `expected_updated_at` token.
- Deletion impact cannot leak between dialog targets.
- Permanent deletion removes all cache entries for the deleted experience.
- Query cancellation reaches the API transport signal.
- Existing dirty navigation, lifecycle, localization, accessibility, and API contract tests continue to pass.

Verification remains:

- Full frontend Vitest suite.
- ESLint.
- TypeScript `--noEmit`.
- Next.js production build.
- Full backend suite because the backend concurrency contract remains part of the end-to-end safety model.

## 10. Dependency and Documentation Changes

- Add `@tanstack/react-query` v5 to `apps/frontend/package.json` and lockfile.
- Do not add devtools or persistence packages.
- Update the frontend API documentation to describe query cancellation and module cache ownership.
- Update the existing personal-experience implementation plan or add a focused migration plan; do not rewrite the original product SPEC.

## 11. Acceptance Criteria

1. No component in `components/experiences/` directly invokes an experience transport function.
2. All experience-library server reads use TanStack queries.
3. All experience-library server writes use TanStack mutations.
4. Hand-written request generation refs and request-only mounted guards are removed.
5. Query reads consume TanStack's abort signal through the central API client.
6. Same-experience writes are serialized and stale backend writes still return 409.
7. Cache writes are authoritative, immutable, and isolated by experience ID/status.
8. Form drafts and dirty-navigation behavior remain local and unchanged in meaning.
9. No other Resume Matcher route is migrated to TanStack Query.
10. All required tests, static checks, and production build pass.
