# i18n Maintenance Guide

> Current Chinese/English localization contract for Resume Matcher.

## Current State

- UI uses statically imported JSON locale files in `messages/`
- Content language preference stored via `LanguageProvider`
- Supported: zh, en (default: zh)

## Translation File Location

```
apps/frontend/messages/
├── zh.json
└── en.json
```

## Supported Locale Contract

Only `zh` and `en` are supported. Adding another locale requires an explicit product decision and coordinated frontend messages, locale registration, backend validation, prompt-language mapping, and parity tests.

## Translation Keys

```json
{
  "dashboard": {
    "title": "Dashboard",
    "masterResume": "Master Resume"
  },
  "builder": {
    "save": "Save",
    "download": "Download PDF"
  }
}
```

## Usage in Components

```tsx
import { useTranslations } from 'next-intl';

export function MyComponent() {
  const t = useTranslations('dashboard');
  return <h1>{t('title')}</h1>;
}
```

## Content Language vs UI Language

- **UI Language:** Controlled by `next-intl`, affects interface text
- **Content Language:** Controlled by `LanguageProvider`, affects LLM-generated content (cover letters, tailored resumes)

## Backend i18n (Future)

Currently prompts are English-only. To support multiple languages:
1. Create `app/i18n/locales/{lang}.json`
2. Add language parameter to prompt templates
3. Pass `Accept-Language` header from frontend
