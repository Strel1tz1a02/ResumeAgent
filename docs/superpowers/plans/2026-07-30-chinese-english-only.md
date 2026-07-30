# Chinese and English Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all project language support except Chinese and English, with Chinese as the default.

**Architecture:** Reduce the frontend locale source of truth and message registry to `zh/en`, then mirror the same contract in backend configuration and prompt language naming. Delete unused locale assets and update tests/tooling/docs so no production path advertises or loads removed languages.

**Tech Stack:** Next.js, React, TypeScript, Vitest, Python, FastAPI, Pytest.

## Global Constraints

- Supported language codes are exactly `zh` and `en`.
- Default and unknown-language fallback is `zh`.
- No persisted-data migration is required.
- Candidate resume fields describing spoken languages remain unchanged.
- Preserve unrelated user changes in `Launch.md` and `AGENT.md`.

---

### Task 1: Reduce the frontend locale contract

**Files:**

- Modify: `apps/frontend/tests/i18n-locale-parity.test.ts`
- Modify: `apps/frontend/tests/i18n-server.test.ts`
- Modify: `apps/frontend/i18n/config.ts`
- Modify: `apps/frontend/lib/i18n/messages.ts`
- Modify: `apps/frontend/lib/api/config.ts`
- Modify: `apps/frontend/app/(default)/dashboard/page.tsx`
- Delete: `apps/frontend/messages/es.json`
- Delete: `apps/frontend/messages/fr.json`
- Delete: `apps/frontend/messages/ja.json`
- Delete: `apps/frontend/messages/pt-BR.json`

**Interfaces:**

- Produces: `Locale = 'zh' | 'en'`
- Produces: `SupportedLanguage = 'zh' | 'en'`
- Produces: `defaultLocale = 'zh'`

- [x] Add tests asserting `locales` equals `['zh', 'en']`, unknown locale translation falls back to `zh`, and only Chinese/English message registries are required.
- [x] Run `npx.cmd vitest run tests/i18n-locale-parity.test.ts tests/i18n-server.test.ts` and verify failure against the six-language implementation.
- [x] Reduce config/types/imports, simplify date locale selection to `zh-CN` or `en-US`, and delete four locale JSON files.
- [x] Run the focused i18n tests and `npx.cmd tsc --noEmit`.

### Task 2: Reduce the backend language contract

**Files:**

- Modify: `apps/backend/tests/integration/test_config_api.py`
- Modify: `apps/backend/tests/unit/test_experience_completeness.py`
- Modify: `apps/backend/app/routers/config.py`
- Modify: `apps/backend/app/schemas/models.py`
- Modify: `apps/backend/app/config_cache.py`
- Modify: `apps/backend/app/prompts/templates.py`

**Interfaces:**

- Produces: `SUPPORTED_LANGUAGES = ['zh', 'en']`
- Produces: language configuration defaults of `zh`
- Produces: `get_language_name(unknown) == 'Chinese (Simplified)'`

- [x] Add tests asserting exact supported languages, Chinese empty-config defaults, rejection of a removed code such as `es`, and Chinese fallback for unknown prompt language codes.
- [x] Run the focused Pytest files and verify failure against the six-language implementation.
- [x] Reduce backend validation/mappings and change language fallbacks/defaults to `zh`.
- [x] Run focused backend tests and compile checks.

### Task 3: Align tooling, docs, and complete verification

**Files:**

- Modify: `scripts/check_locale_parity.py`
- Modify: `apps/frontend/CLAUDE.md`
- Modify: `docs/agent/features/i18n.md`
- Modify: `docs/agent/architecture/frontend-architecture.md`
- Modify: `docs/superpowers/plans/2026-07-30-chinese-english-only.md`

**Interfaces:**

- Documents and verifies the exact two-language contract.

- [x] Update current documentation and parity-tool comments from multi-locale assumptions to Chinese/English.
- [x] Search production code for removed locale registrations and confirm none remain; ignore candidate spoken-language examples and historical plans.
- [x] Run frontend tests, lint, TypeScript, build, backend tests, compileall, locale parity, and `git diff --check`.
- [x] Mark this execution checklist complete and commit only intended files.
