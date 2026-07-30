# Experience Creation Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give both Personal Experience Library creation entries explicit labels in every supported locale.

**Architecture:** Keep the existing UI and localization pipeline. Replace the ambiguous template-action key, add a nested text-import button key, and update behavior tests before production translations/components.

**Tech Stack:** Next.js, React, JSON locale catalogs, Vitest, Testing Library.

## Global Constraints

- Change only Personal Experience Library labels, locale catalogs, and their tests.
- Preserve creation/import behavior and layout.

---

### Task 1: Localize both creation actions

**Files:**

- Modify: `apps/frontend/tests/experience-library-page.test.tsx`
- Modify: `apps/frontend/components/experiences/experience-library-page.tsx`
- Modify: `apps/frontend/messages/{en,zh,ja,fr,es,pt-BR}.json`

**Interfaces:**

- Produces: `experiences.createFromTemplate`
- Produces: `experiences.import.button`

- [x] Add a test translation map and assertions for `Create from Template` and `Import Text`.
- [x] Run the focused test and verify it fails because the component still reads the old keys.
- [x] Update the component and all six locale catalogs with native labels.
- [x] Run focused tests, locale validation, lint, TypeScript, and the complete frontend suite.
- [x] Commit the implementation.
