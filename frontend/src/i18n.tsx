import { useSyncExternalStore } from 'react';
import { getLocale, subscribeLocale, setLocale, translate, formatNumber, formatDate } from './i18n-core';

export function useI18n() {
  const locale = useSyncExternalStore(subscribeLocale, getLocale, () => 'ru' as const);
  return { locale, setLocale, t: translate, number: formatNumber, date: formatDate };
}
