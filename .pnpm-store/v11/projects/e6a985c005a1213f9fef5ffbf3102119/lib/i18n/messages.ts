import type { Locale } from '@/i18n/config';

import en from '@/messages/en.json';
import zh from '@/messages/zh.json';

export type Messages = typeof en;

const allMessages: Record<Locale, Messages> = {
  zh,
  en,
};

export function getMessages(locale: Locale): Messages {
  return allMessages[locale] || allMessages.zh;
}
