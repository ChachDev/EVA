// Static product pages contain no customer data and work without JavaScript.
export const publicLocales = ['ru', 'kk', 'en'] as const;
const copy = {
  ru: {
    title: 'EVA — аудит качества клиентских диалогов',
    description: 'Локальный аудит чатов и транскриптов звонков: настраиваемые чек-листы, оценки с доказательствами и апелляции с сохранением истории.',
    eyebrow: 'Expert Virtual Auditor', headline: 'Каждая оценка — с обоснованием.',
    intro: 'EVA помогает команде контроля качества оценивать работу операторов по единым критериям, проверять выводы по исходным репликам и разбирать спорные результаты.',
    open: 'Открыть рабочее пространство', theme: 'Сменить тему', language: 'Язык',
    features: [
      ['Ваши критерии', 'Редактируйте инструкции, веса, шкалы и условия N/A. Каждая опубликованная версия чек-листа сохраняется.'],
      ['Проверяемые выводы', 'Переходите от оценки к конкретным репликам. Модель интерпретирует текст, а сервер проверяет ответ и рассчитывает итог.'],
      ['Прозрачный пересмотр', 'Рассматривайте апелляции с причиной и доказательствами. Исходная оценка и пересмотренный результат хранятся отдельно.'],
    ],
    workflow: 'От разговора к решению', steps: ['Добавьте чат или готовый транскрипт.', 'Выберите чек-лист и запустите оценку.', 'Проверьте доказательства и рассмотрите апелляции.'],
    localTitle: 'Локальная обработка, три языка интерфейса',
    localText: 'Интерфейс доступен на русском, казахском и английском. EVA использует локальную модель через llama.cpp. Текст исходного диалога и критерии сохраняют свой язык.',
    statusTitle: 'Что важно перед использованием',
    statusText: 'Проект создан для CQA-хакатона Freedom Broker. Автоматические оценки предварительные; точность модели на RU/KZ/EN требует независимой проверки. Распознавание аудио требует отдельного ASR-провайдера. Публичный кабинет без аутентификации не предназначен для реальных клиентских данных.',
    footer: 'EVA · Expert Virtual Auditor · Локальный прототип',
  },
  kk: {
    title: 'EVA — клиенттермен диалогтардың сапасын бағалау',
    description: 'Чаттар мен қоңырау транскрипттерін жергілікті бағалау: бапталатын тексеру тізімдері, дәлелді бағалар және тарихы сақталатын апелляциялар.',
    eyebrow: 'Expert Virtual Auditor', headline: 'Әр баға дәлелге сүйенеді.',
    intro: 'EVA сапаны бақылау тобына операторлардың жұмысын бірыңғай критерийлермен бағалауға, қорытындыларды бастапқы репликалар арқылы тексеруге және даулы нәтижелерді қарауға көмектеседі.',
    open: 'Жұмыс кеңістігін ашу', theme: 'Тақырыпты ауыстыру', language: 'Тіл',
    features: [
      ['Сіздің критерийлеріңіз', 'Нұсқауларды, салмақтарды, шкалаларды және N/A шарттарын өзгертіңіз. Тексеру тізімінің әр жарияланған нұсқасы сақталады.'],
      ['Тексерілетін қорытындылар', 'Бағадан нақты репликаларға өтіңіз. Модель мәтінді түсіндіреді, сервер жауапты тексеріп, қорытынды балды есептейді.'],
      ['Ашық қайта қарау', 'Апелляцияларды себептері мен дәлелдері арқылы қараңыз. Бастапқы баға мен қайта қаралған нәтиже бөлек сақталады.'],
    ],
    workflow: 'Диалогтан шешімге дейін', steps: ['Чат не дайын транскрипт қосыңыз.', 'Тексеру тізімін таңдап, бағалауды бастаңыз.', 'Дәлелдерді тексеріп, апелляцияларды қараңыз.'],
    localTitle: 'Жергілікті өңдеу, интерфейстің үш тілі',
    localText: 'Интерфейс қазақ, орыс және ағылшын тілдерінде қолжетімді. EVA llama.cpp арқылы жергілікті модельді қолданады. Бастапқы диалог мәтіні мен критерийлер өз тілінде қалады.',
    statusTitle: 'Қолданар алдында ескеріңіз',
    statusText: 'Жоба Freedom Broker CQA-хакатоны үшін жасалған. Автоматты бағалар алдын ала берілген; RU/KZ/EN бойынша модель дәлдігін тәуелсіз тексеру қажет. Аудионы тану үшін бөлек ASR провайдері керек. Аутентификациясыз ашық кабинет нақты клиент деректеріне арналмаған.',
    footer: 'EVA · Expert Virtual Auditor · Жергілікті прототип',
  },
  en: {
    title: 'EVA — customer conversation quality auditing',
    description: 'Audit chats and call transcripts locally with configurable checklists, evidence-linked assessments and appeals that preserve the original record.',
    eyebrow: 'Expert Virtual Auditor', headline: 'An explanation behind every assessment.',
    intro: 'EVA helps quality teams assess operators against consistent criteria, verify findings against the original messages and resolve disputed results.',
    open: 'Open workspace', theme: 'Switch theme', language: 'Language',
    features: [
      ['Your criteria', 'Edit instructions, weights, scales and N/A conditions. Every published checklist version is preserved.'],
      ['Verifiable findings', 'Follow each assessment to specific messages. The model interprets the text; the server validates its response and calculates the score.'],
      ['Transparent review', 'Resolve appeals with reasons and evidence. Original assessments and revised results remain separate.'],
    ],
    workflow: 'From conversation to decision', steps: ['Add a chat or an existing transcript.', 'Choose a checklist and run an assessment.', 'Check the evidence and review appeals.'],
    localTitle: 'Local processing, three interface languages',
    localText: 'The interface is available in Kazakh, Russian and English. EVA uses a local model through llama.cpp. Source conversations and criteria keep their original language.',
    statusTitle: 'Before you start',
    statusText: 'Built for the Freedom Broker CQA hackathon. Automated assessments are preliminary; model accuracy across RU/KZ/EN needs independent validation. Speech recognition requires a separate ASR provider. A public workspace without authentication is not intended for real customer data.',
    footer: 'EVA · Expert Virtual Auditor · Local prototype',
  },
};
const escape = (value: string) => value.replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!);
export function validatePublicOrigin(value: string): string {
  if (!value.trim()) return '';
  const url = new URL(value);
  const host = url.hostname.replace(/\.$/, '');
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || url.pathname !== '/' || !host.includes('.') || host.endsWith('.localhost') || host.endsWith('.local') || /^\d+\.\d+\.\d+\.\d+$/.test(host) || host.startsWith('[')) throw new Error('VITE_PUBLIC_SITE_URL must be a public HTTPS origin without path, credentials, query or fragment.');
  url.hostname = host;
  return url.origin;
}
export function renderProductPage(locale: typeof publicLocales[number], origin = ''): string {
  const c = copy[locale];
  const href = (lang: string) => `${origin}/about/${lang}/`;
  const canonical = origin ? `<link rel="canonical" href="${escape(href(locale))}">${publicLocales.map(lang => `<link rel="alternate" hreflang="${lang}" href="${escape(href(lang))}">`).join('')}<link rel="alternate" hreflang="x-default" href="${escape(href('ru'))}"><meta property="og:url" content="${escape(href(locale))}"><meta property="og:image" content="${escape(origin)}/brand/eva-logo.png">` : '';
  return `<!doctype html><html lang="${locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${escape(c.title)}</title><meta name="description" content="${escape(c.description)}"><meta name="robots" content="index, follow"><meta name="theme-color" content="#10291e"><meta property="og:type" content="website"><meta property="og:site_name" content="EVA — Expert Virtual Auditor"><meta property="og:title" content="${escape(c.title)}"><meta property="og:description" content="${escape(c.description)}"><meta property="og:locale" content="${{ ru: 'ru_RU', kk: 'kk_KZ', en: 'en_GB' }[locale]}"><meta name="twitter:card" content="summary"><meta name="twitter:title" content="${escape(c.title)}"><meta name="twitter:description" content="${escape(c.description)}">${canonical}<link rel="icon" href="/favicon.svg"><link rel="stylesheet" href="/product.css"><script src="/product-preferences.js" defer></script></head><body><header><a class="wordmark" href="/about/${locale}/">EVA<span>Expert Virtual Auditor</span></a><nav aria-label="${c.language}">${publicLocales.map(lang => `<a lang="${lang}" hreflang="${lang}" ${lang === locale ? 'aria-current="page"' : ''} href="/about/${lang}/">${lang === 'kk' ? 'KZ' : lang.toUpperCase()}</a>`).join('')}<button id="theme-switch" type="button" aria-label="${c.theme}">◐</button></nav></header><main><section class="hero"><p class="eyebrow">${c.eyebrow}</p><h1>${c.headline}</h1><p class="intro">${c.intro}</p><a class="cta" href="/#/overview" data-workspace-language="${locale}">${c.open} <span aria-hidden="true">↗</span></a></section><section class="features">${c.features.map(([title, text], index) => `<article><span class="number">0${index + 1}</span><h2>${title}</h2><p>${text}</p></article>`).join('')}</section><section class="workflow"><h2>${c.workflow}</h2><ol>${c.steps.map(step => `<li>${step}</li>`).join('')}</ol></section><section class="context"><h2>${c.localTitle}</h2><p>${c.localText}</p><h2>${c.statusTitle}</h2><p>${c.statusText}</p></section></main><footer>${c.footer}</footer></body></html>`;
}
export function renderSitemap(origin: string): string {
  return `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${publicLocales.map(locale => `<url><loc>${escape(origin)}/about/${locale}/</loc></url>`).join('')}</urlset>`;
}
export function renderRobots(origin = '') {
  return `User-agent: *\nAllow: /\nDisallow: /api/\n${origin ? `Sitemap: ${origin}/sitemap.xml\n` : ''}`;
}
