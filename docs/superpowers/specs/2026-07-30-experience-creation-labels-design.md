# Experience Creation Entry Labels Design

## Goal

Make the two Personal Experience Library creation entries unambiguous and fully localized. The template-based empty draft entry and free-text import entry must never display raw translation keys.

## Scope

Only the Personal Experience Library header actions and their localization/test coverage change. Creation behavior, dialogs, API calls, TanStack Query ownership, and backend contracts remain unchanged.

## Translation Contract

- Replace the ambiguous `experiences.create` key with `experiences.createFromTemplate`.
- Add the explicit button key `experiences.import.button`; keep the existing nested import dialog keys unchanged.
- Chinese labels are exactly `模板创建` and `文本导入`.
- English labels are `Create from Template` and `Import Text`.
- Japanese, French, Spanish, and Brazilian Portuguese receive equivalent native translations so both actions follow the configured system language in every supported locale.

## UI and Testing

`ExperienceLibraryPage` reads both labels through the existing `useTranslations()` path. Tests use the new semantic keys and verify both actions render with their localized labels while preserving the existing creation and import behavior.

## Non-goals

- No layout, styling, icon, dialog-flow, or backend changes.
- No rename of import dialog fields such as title, description, submit, or error unless required for key nesting consistency.
