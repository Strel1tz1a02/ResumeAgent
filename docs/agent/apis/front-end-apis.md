# Frontend API Client

> API client layer for Resume Matcher frontend.

## Base Client (`lib/api/client.ts`)

```typescript
export const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
export const API_BASE = `${API_URL}/api/v1`;

export async function apiFetch(endpoint: string, options?: RequestInit);
export async function apiPost<T>(endpoint: string, body: T);
export async function apiPatch<T>(endpoint: string, body: T);
export async function apiPut<T>(endpoint: string, body: T);
export async function apiDelete(endpoint: string);
export function getUploadUrl(): string;
```

## Resume Operations (`lib/api/resume.ts`)

```typescript
// Job descriptions
uploadJobDescriptions(descriptions: string[], resumeId: string) → job_id

// Resume improvement
improveResume(resumeId: string, jobId: string) → ImprovedResult

// CRUD
fetchResume(resumeId: string) → ResumeResponse['data']
fetchResumeList(includeMaster?: boolean) → ResumeListItem[]
updateResume(resumeId: string, data: ResumeData) → ResumeResponse['data']
deleteResume(resumeId: string) → void

// PDF
downloadResumePdf(resumeId: string, settings?: TemplateSettings) → Blob
downloadCoverLetterPdf(resumeId: string, pageSize?: string) → Blob

// Content updates
updateCoverLetter(resumeId: string, content: string) → void
updateOutreachMessage(resumeId: string, content: string) → void

// On-demand generated content
generateInterviewPrep(resumeId: string) → InterviewPrepData
```

## Resume Wizard (`lib/api/resume-wizard.ts`)

```typescript
postResumeWizardTurn(payload: ResumeWizardTurnRequest) → ResumeWizardTurnResponse
finalizeResumeWizard(state: ResumeWizardState) → ResumeWizardFinalizeResponse
createInitialResumeWizardState() → ResumeWizardState
```

Backend endpoints:

- `POST /api/v1/resume-wizard/turn` — one adaptive turn. `action` is `start | answer | skip | back | review`. `answer`/`skip` run one AI call that updates `resume_data`, returns the next `current_question`, `inferred_skills`, and an `is_complete` flag; `back`/`review`/`start` are deterministic (no LLM). The full `ResumeWizardState` round-trips in the request and response.
- `POST /api/v1/resume-wizard/finalize` — creates the single master resume from the draft (`processing_status: "ready"`), or `409` if a master already exists.

The wizard is an AI-led, one-question-at-a-time flow that builds a general master resume; it does not require a job description and does not replace the upload parser. Question and content text are produced in the configured **content language**; static UI chrome uses the `resumeWizard.*` i18n keys.

## Personal Experience Library (`lib/api/experiences.ts`)

All paths below use the existing `/api/v1` base client. An experience is person-level factual material and is not attached to a resume.

```typescript
// Query and CRUD
listExperiences({ q?, kind?, status?, sort? }) → { items: ExperienceRead[]; total: number }
importExperienceText(text) → ExperienceDetail
createExperience(payload) → ExperienceDetail
fetchExperience(experienceId) → ExperienceDetail
patchExperience(experienceId, { ...editableFields, expected_updated_at? }) → ExperienceDetail

// Evidence and lifecycle
createEvidence(experienceId, { action, result?, metrics? }) → ExperienceDetail
patchEvidence(experienceId, evidenceId, payload) → ExperienceDetail
deleteEvidence(experienceId, evidenceId) → ExperienceDetail
reorderEvidence(experienceId, evidenceIds) → ExperienceDetail
markExperienceReady(experienceId) → ExperienceDetail
archiveExperience(experienceId) → ExperienceDetail
restoreExperience(experienceId) → ExperienceDetail
getDeletionImpact(experienceId) → DeletionImpactResponse
deleteExperiencePermanently(experienceId) → void

// Stateless, one-question-at-a-time AI enrichment
requestNextExperienceQuestion(experienceId) → { question_id, question, target, evidence_id, is_fallback }
submitExperienceAnswer(experienceId, { question_id, answer, evidence_id? }) → ExperienceDetail & { next_question }
```

Backend endpoints:

- `GET /experiences` — accepts `q`, `kind`, `status=active|draft|ready|archived`, and `sort=updated_at_desc|created_at_desc|created_at_asc`.
- `POST /experiences` and `PATCH /experiences/{experience_id}` — create or update editable fields. The frontend's “New experience” action posts `{}` and immediately selects the returned blank draft. PATCH may include `expected_updated_at` as an optimistic-concurrency token; a stale snapshot returns `409` instead of overwriting newer manual or AI facts. IDs, status, timestamps, evidence references, and completeness remain server-owned.
- `POST /experiences/import-text` with `{ "text": string }` — stores the exact nonblank text (maximum 20,000 characters) as a draft before any AI call and returns `201` with expanded detail.
- `GET /experiences/{experience_id}` — returns ordered `evidence_ids`, expanded `evidence_items`, persisted completeness, and natural-language derived questions in the configured content language. Missing-dimension keys remain stable machine-readable identifiers.
- `POST /experiences/{experience_id}/evidence`, `PATCH|DELETE /experiences/{experience_id}/evidence/{evidence_id}`, and `PUT /experiences/{experience_id}/evidence-order` — keep action/result/metrics together. Reordering must be an exact, duplicate-free permutation of the currently owned IDs. Every mutation returns refreshed expanded detail.
- `POST /experiences/{experience_id}/mark-ready` — requires server completeness of at least 60; otherwise returns `409` with `{ completeness, missing_dimensions }`.
- `POST /experiences/{experience_id}/archive` — the normal delete action. `POST /restore` returns an archived item to `draft`.
- `GET /experiences/{experience_id}/deletion-impact` — currently returns empty arrays while preserving the future shape `affected_matches: Array<{ match_id, job_title }>` plus `affected_resumes`.
- `DELETE /experiences/{experience_id}/permanent` — allowed only after archive and returns `204`. It deletes owned evidence transactionally but never edits or deletes existing resumes.
- `POST /experiences/{experience_id}/questions/next` and `POST /experiences/{experience_id}/answers` — use current persisted facts plus the latest answer, validate a narrow typed patch, and never store conversation history. Every question identifies `target: "experience" | "evidence"` and an optional owned `evidence_id`; the answer echoes that ID, so model output cannot redirect a patch to another evidence row. Question generation can return localized `is_fallback: true` guidance; answer failures are retryable and leave stored state unchanged.

Validation errors use `422`, missing resources use `404`, lifecycle conflicts use `409`, and retryable answer-enrichment failures use `503`.

### Personal Experience Library query ownership

Only `/experiences` uses the feature-scoped TanStack Query v5 client under `lib/queries/experiences/`; no application-wide provider is installed. Its canonical caches are status lists, detail by `experience_id`, and deletion impact by `experience_id`.

- List, detail, and deletion-impact query functions pass TanStack's `AbortSignal` through the API client. The client combines caller cancellation with its existing request timeout and does not report a caller cancellation as a timeout.
- Window-focus and reconnect refetching, query retries, and mutation retries are disabled for this route. Cached authoritative responses remain fresh until a mutation, explicit refresh, or lifecycle invalidation changes them.
- Manual create, text import, metadata, evidence, AI, ready, archive, restore, and permanent-delete calls are TanStack mutations. Mutations for one experience share `scope.id = "experience:{id}"` and execute serially; different experiences may mutate independently.
- Before applying a successful write response, the query layer cancels status-list reads and only the matching experience's detail read; unrelated detail and deletion-impact reads continue independently. The returned `ExperienceDetail` then updates the matching detail and status-list caches immutably. Permanent deletion additionally cancels that experience's impact read and removes only its cache entries.
- Form drafts, dirty-navigation guards, filters, dialogs, and transient AI question/answer UI remain component-local. Metadata saves still send `expected_updated_at`; TanStack serialization complements rather than replaces the backend `409` stale-write check.

## Application Tracker (`lib/api/tracker.ts`)

```typescript
// Kanban board (7 status columns: saved | applied | no_response |
// response | interview | accepted | rejected)
listApplications() → ApplicationListResponse        // { columns: Record<status, Application[]> }
createApplication(payload: ManualApplicationCreate) → Application   // manual add from a pasted JD
getApplicationDetail(id: string) → ApplicationDetail               // embedded JD + applied resume (resume null if deleted)
updateApplication(id: string, payload: ApplicationUpdate) → Application   // status/position/notes/company/role/applied_at

// Bulk
bulkUpdateStatus(applicationIds: string[], status: ApplicationStatus) → ApplicationActionResponse
deleteApplication(id: string) → void
bulkDeleteApplications(applicationIds: string[]) → ApplicationActionResponse
```

## Config Operations (`lib/api/config.ts`)

```typescript
fetchLlmConfig() → LLMConfig
updateLlmConfig(config: LLMConfigUpdate) → LLMConfig
testLlmConnection() → LLMHealthCheck
fetchSystemStatus() → SystemStatus

// Per-provider API keys (encrypted server-side; switching the active
// provider no longer wipes another provider's key — responses always masked)
fetchApiKeyStatus() → ApiKeyStatusResponse           // { providers: [{ provider, configured, masked_key }] }
updateApiKeys(keys: ApiKeysUpdateRequest) → ApiKeysUpdateResponse
deleteApiKey(provider: ApiKeyProvider) → void
clearAllApiKeys() → void

// Feature flags
fetchFeatureConfig() → FeatureConfig
updateFeatureConfig(config: FeatureConfigUpdate) → FeatureConfig

// Language
fetchLanguageConfig() → LanguageConfig
updateLanguageConfig(language: string) → LanguageConfig
```

> `updateLlmApiKey` (`PUT /config/llm-api-key`) no longer persists a key — keys are managed per-provider via the encrypted `/config/api-keys` endpoints above.

## Provider Info

```typescript
export const PROVIDER_INFO = {
  openai: { name: 'OpenAI', defaultModel: 'gpt-5-nano-2025-08-07', requiresKey: true },
  anthropic: { name: 'Anthropic', defaultModel: 'claude-haiku-4-5-20251001', requiresKey: true },
  openrouter: { name: 'OpenRouter', defaultModel: 'deepseek/deepseek-chat', requiresKey: true },
  gemini: { name: 'Google Gemini', defaultModel: 'gemini-3-flash-preview', requiresKey: true },
  deepseek: { name: 'DeepSeek', defaultModel: 'deepseek-chat', requiresKey: true },
  ollama: { name: 'Ollama (Local)', defaultModel: 'gemma3:4b', requiresKey: false },
};
```

## Usage

```typescript
import { fetchResume, API_BASE, PROVIDER_INFO } from '@/lib/api';
```
