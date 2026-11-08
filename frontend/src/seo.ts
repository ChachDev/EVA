import type { Locale } from './i18n-core';

const descriptions: Record<Locale, string> = {
  ru: 'EVA — локальный аудит клиентских диалогов: настраиваемые чек-листы, оценки с доказательствами и апелляции с сохранением истории.',
  kk: 'EVA — клиенттермен диалогтарды жергілікті бағалау: бапталатын тексеру тізімдері, дәлелді бағалар және тарихы сақталатын апелляциялар.',
  en: 'EVA audits customer conversations locally with configurable checklists, evidence-linked assessments and appeals that preserve the original record.',
};
const routes: Record<string, Record<Locale, string>> = {
  overview: { ru: 'Рабочее пространство', kk: 'Жұмыс кеңістігі', en: 'Workspace' },
  dialogs: { ru: 'Диалоги', kk: 'Диалогтар', en: 'Conversations' },
  checklists: { ru: 'Чек-листы', kk: 'Тексеру тізімдері', en: 'Checklists' },
  appeals: { ru: 'Апелляции', kk: 'Апелляциялар', en: 'Appeals' },
  audio: { ru: 'Аудиозаписи', kk: 'Аудиожазбалар', en: 'Audio recordings' },
  brand: { ru: 'Дизайн-система', kk: 'Дизайн жүйесі', en: 'Design system' },
};
export function updateMetadata(locale: Locale) {
  // Never put conversation IDs, client text or evaluation content in share metadata.
  const route = location.hash.replace(/^#\/?/, '').split(/[/?]/)[0];
  const title = `${(routes[route] ?? routes.overview)[locale]} · EVA — Expert Virtual Auditor`;
  document.title = title;
  for (const selector of ['meta[property="og:title"]', 'meta[name="twitter:title"]']) document.querySelector(selector)?.setAttribute('content', title);
  for (const selector of ['meta[name="description"]', 'meta[property="og:description"]', 'meta[name="twitter:description"]']) document.querySelector(selector)?.setAttribute('content', descriptions[locale]);
  document.querySelector('meta[property="og:locale"]')?.setAttribute('content', { ru: 'ru_RU', kk: 'kk_KZ', en: 'en_GB' }[locale]);
}
