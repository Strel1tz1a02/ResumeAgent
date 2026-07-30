# Chinese and English Only Language Design

## Goal

Reduce the entire Resume Matcher language surface to Chinese and English, with Chinese as the default.

## Scope

- Frontend UI locale selection, message loading, locale types, print-page fallback, and date formatting.
- Backend language configuration validation, defaults, and advertised supported languages.
- Tests, parity tooling, and current architecture/i18n documentation.

## Behavior

- Supported language codes are exactly `zh` and `en`.
- The default UI and generated-content language is `zh`.
- Remove Spanish, French, Japanese, and Brazilian Portuguese message files and all production imports/registrations for them.
- Settings exposes only Chinese and English for both UI language and generated-content language.
- Unknown language values resolve to Chinese.
- There is no database or persisted-language migration because the current environment contains no other-language data.

## Non-goals

- Resume data fields describing a candidate's spoken languages are unaffected.
- Historical implementation plans are not rewritten.
- No layout or unrelated localization-copy changes.

## Verification

- Tests assert the exact supported language set and Chinese fallback/default behavior.
- Frontend locale parity, full frontend tests, lint, TypeScript, and production build pass with only `zh.json` and `en.json`.
- Backend configuration and experience-language tests pass with the reduced language set.
