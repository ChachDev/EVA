import { appMessages } from './locales/app.ts';
import { workflowMessages } from './locales/workflows.ts';
import { commonMessages } from './locales/common.ts';
import { detailMessages } from './locales/detail.ts';
import { overviewMessages } from './locales/overview.ts';
import { editorMessages } from './locales/editor.ts';
import { workspaceMessages } from './locales/workspace.ts';

export const locales = ['ru', 'kk', 'en'] as const;
export type Locale = typeof locales[number];
export type Params = Record<string, string | number>;
export const localeTags: Record<Locale, string> = { ru: 'ru-RU', kk: 'kk-KZ', en: 'en-GB' };
export const LANGUAGE_KEY = 'eva.ui.language';
export const messages = { ...commonMessages, ...appMessages, ...workflowMessages, ...detailMessages, ...overviewMessages, ...editorMessages, ...workspaceMessages };

export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && (locales as readonly string[]).includes(value);
}
export function detectLocale(saved: unknown, preferred: readonly string[] = []): Locale {
  if (isLocale(saved)) return saved;
  for (const tag of preferred) {
    const code = tag.toLowerCase().split('-')[0];
    if (code === 'kz' || code === 'kk') return 'kk';
    if (isLocale(code)) return code;
  }
  return 'ru';
}
function initialLocale(): Locale {
  if (typeof window === 'undefined') return 'ru';
  let stored: string | null = null;
  try { stored = window.localStorage.getItem(LANGUAGE_KEY); } catch { /* Storage is optional. */ }
  return detectLocale(stored, window.navigator.languages);
}
let currentLocale = initialLocale();
const subscribers = new Set<() => void>();
export const getLocale = () => currentLocale;
export function subscribeLocale(callback: () => void) {
  subscribers.add(callback);
  return () => { subscribers.delete(callback); };
}
export function setLocale(locale: Locale, persist = true) {
  if (!isLocale(locale)) return;
  currentLocale = locale;
  if (persist && typeof window !== 'undefined') {
    try { window.localStorage.setItem(LANGUAGE_KEY, locale); } catch { /* Keep the current session usable. */ }
  }
  subscribers.forEach(callback => callback());
}
export function translate(source: string, params: Params = {}, locale: Locale = currentLocale): string {
  const entry = (messages as Record<string, { kk: string; en: string }>)[source];
  const text = locale === 'ru' ? source : entry?.[locale] ?? source;
  return text.replace(/\{(\w+)\}/g, (match, key: string) => Object.hasOwn(params, key) ? String(params[key]) : match);
}
export function formatNumber(value: number | null | undefined, options: Intl.NumberFormatOptions = {}, locale = currentLocale) {
  return value == null ? '—' : new Intl.NumberFormat(localeTags[locale], { maximumFractionDigits: 2, ...options }).format(value);
}
export function formatDate(value: string | number | Date, options: Intl.DateTimeFormatOptions = {}, locale = currentLocale) {
  const date = value instanceof Date ? value : new Date(value);
  const fields = ['weekday', 'era', 'year', 'month', 'day', 'hour', 'minute', 'second', 'fractionalSecondDigits', 'timeZoneName'];
  const useDefaultStyles = !fields.some(field => Object.hasOwn(options, field)) && !options.dateStyle && !options.timeStyle;
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat(localeTags[locale], {
    ...(useDefaultStyles ? { dateStyle: 'short' as const, timeStyle: 'short' as const } : {}), ...options,
  }).format(date);
}
