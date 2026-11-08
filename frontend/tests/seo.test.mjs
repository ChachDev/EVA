import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import {
  publicLocales, renderProductPage, renderRobots, renderSitemap, validatePublicOrigin,
} from '../seo-pages.ts';
import { updateMetadata } from '../src/seo.ts';

const origin = 'https://eva.example';
const expected = {
  ru: { headline: 'Каждая оценка — с обоснованием.', feature: 'Ваши критерии', language: 'ru_RU' },
  kk: { headline: 'Әр баға дәлелге сүйенеді.', feature: 'Сіздің критерийлеріңіз', language: 'kk_KZ' },
  en: { headline: 'An explanation behind every assessment.', feature: 'Your criteria', language: 'en_GB' },
};
const tags = (html, name) => [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, 'g'))].map(match => match[0]);
const attribute = (tag, name) => tag.match(new RegExp(`\\b${name}="([^"]*)"`))?.[1];
const headOf = html => html.match(/<head>([\s\S]*?)<\/head>/)?.[1] ?? '';
function metadata(html, name) {
  const meta = tags(headOf(html), 'meta').find(tag => attribute(tag, 'name') === name || attribute(tag, 'property') === name);
  return meta ? attribute(meta, 'content') : undefined;
}

test('three public language pages provide actual localized HTML without running JavaScript', () => {
  assert.deepEqual([...publicLocales].sort(), ['en', 'kk', 'ru']);
  for (const locale of publicLocales) {
    const html = renderProductPage(locale);
    assert.match(html, /^<!doctype html>/i);
    assert.equal(attribute(tags(html, 'html')[0], 'lang'), locale);
    assert.ok(html.includes(`<h1>${expected[locale].headline}</h1>`), `${locale}: localized page heading`);
    assert.ok(html.includes(`<h2>${expected[locale].feature}</h2>`), `${locale}: localized feature content`);
    const main = html.match(/<main>([\s\S]*?)<\/main>/)?.[1];
    assert.ok(main, `${locale}: semantic main landmark`);
    assert.ok(main.replace(/<[^>]+>/g, '').trim().length > 500, `${locale}: meaningful server-rendered product copy`);
    assert.equal((html.match(/<h1\b/g) ?? []).length, 1);
    assert.ok(tags(html, 'a').some(tag => attribute(tag, 'href') === '/#/overview'), `${locale}: working app link`);
    assert.doesNotMatch(html, /id="eva-root"|\/src\/main\.tsx|DEMO-RU-001|Алия Садыкова|Идентификация клиента успешно пройдена/);
  }
});

test('public pages have matching localized search and sharing metadata', () => {
  const titles = new Set();
  const descriptions = new Set();
  for (const locale of publicLocales) {
    const html = renderProductPage(locale);
    const title = headOf(html).match(/<title>([^<]+)<\/title>/)?.[1];
    const description = metadata(html, 'description');
    assert.ok(title?.includes('EVA'), `${locale}: product title`);
    assert.ok(description && description.length > 60, `${locale}: useful description`);
    assert.equal(metadata(html, 'robots'), 'index, follow');
    assert.equal(metadata(html, 'og:title'), title);
    assert.equal(metadata(html, 'twitter:title'), title);
    assert.equal(metadata(html, 'og:description'), description);
    assert.equal(metadata(html, 'twitter:description'), description);
    assert.equal(metadata(html, 'og:type'), 'website');
    assert.equal(metadata(html, 'og:locale'), expected[locale].language);
    titles.add(title);
    descriptions.add(description);
  }
  assert.equal(titles.size, 3);
  assert.equal(descriptions.size, 3);
});

test('native language navigation works without JavaScript and identifies the current page', () => {
  for (const locale of publicLocales) {
    const links = tags(renderProductPage(locale), 'a').filter(tag => attribute(tag, 'hreflang'));
    assert.equal(links.length, 3);
    assert.deepEqual(links.map(tag => attribute(tag, 'hreflang')).sort(), ['en', 'kk', 'ru']);
    for (const link of links) {
      const language = attribute(link, 'hreflang');
      assert.equal(attribute(link, 'href'), `/about/${language}/`);
      assert.equal(attribute(link, 'lang'), language);
      assert.equal(attribute(link, 'aria-current'), language === locale ? 'page' : undefined);
    }
  }
});

test('an unconfigured deployment does not invent canonical, sharing image or sitemap URLs', () => {
  for (const locale of publicLocales) {
    const html = renderProductPage(locale);
    const head = headOf(html);
    assert.ok(!tags(head, 'link').some(tag => ['canonical', 'alternate'].includes(attribute(tag, 'rel'))));
    assert.equal(metadata(html, 'og:url'), undefined);
    assert.equal(metadata(html, 'og:image'), undefined);
    assert.doesNotMatch(head, /https?:\/\/(?:localhost|127\.0\.0\.1|example\.com)/);
  }
  assert.doesNotMatch(renderRobots(), /^Sitemap:/m);
});

test('a configured public origin produces absolute self-canonicals and reciprocal hreflang', () => {
  const expectedAlternates = Object.fromEntries([
    ...publicLocales.map(locale => [locale, `${origin}/about/${locale}/`]),
    ['x-default', `${origin}/about/ru/`],
  ]);
  for (const locale of publicLocales) {
    const html = renderProductPage(locale, validatePublicOrigin(origin));
    const links = tags(headOf(html), 'link');
    const canonicals = links.filter(tag => attribute(tag, 'rel') === 'canonical');
    assert.equal(canonicals.length, 1);
    assert.equal(attribute(canonicals[0], 'href'), `${origin}/about/${locale}/`);
    const alternates = links.filter(tag => attribute(tag, 'rel') === 'alternate');
    assert.equal(alternates.length, 4);
    assert.deepEqual(Object.fromEntries(alternates.map(tag => [attribute(tag, 'hreflang'), attribute(tag, 'href')])), expectedAlternates);
    assert.equal(metadata(html, 'og:url'), `${origin}/about/${locale}/`);
    assert.equal(metadata(html, 'og:image'), `${origin}/brand/eva-logo.png`);
  }
});

test('sitemap includes only the three public product pages and robots references it', () => {
  const sitemap = renderSitemap(origin);
  assert.match(sitemap, /^<\?xml version="1\.0" encoding="UTF-8"\?>/);
  assert.match(sitemap, /xmlns="http:\/\/www\.sitemaps\.org\/schemas\/sitemap\/0\.9"/);
  const urls = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map(match => match[1]);
  assert.deepEqual(urls.sort(), publicLocales.map(locale => `${origin}/about/${locale}/`).sort());
  assert.doesNotMatch(sitemap, /#|\/api\/|\/dialogs|\/appeals|\/audio|evaluation|localhost/);
  const robots = renderRobots(origin);
  assert.match(robots, /^User-agent: \*$/m);
  assert.match(robots, /^Disallow: \/api\/$/m);
  assert.deepEqual(robots.split('\n').filter(line => line.startsWith('Sitemap:')), [`Sitemap: ${origin}/sitemap.xml`]);
});

test('workspace HTML is noindex while still linking the public product pages', async () => {
  const html = await readFile(new URL('../index.html', import.meta.url), 'utf8');
  assert.equal(metadata(html, 'robots'), 'noindex, nofollow');
  assert.equal(metadata(html, 'referrer'), 'same-origin');
  assert.ok(!tags(headOf(html), 'link').some(tag => attribute(tag, 'rel') === 'canonical'));
  for (const locale of publicLocales) assert.ok(html.includes(`href="/about/${locale}/"`));
});

test('workspace metadata uses route labels and never customer IDs, query values or transcript text', () => {
  const oldDocument = Object.getOwnPropertyDescriptor(globalThis, 'document');
  const oldLocation = Object.getOwnPropertyDescriptor(globalThis, 'location');
  const recorded = new Map();
  const document = {
    title: '',
    querySelector: selector => ({ setAttribute: (name, value) => recorded.set(`${selector}:${name}`, value) }),
  };
  Object.defineProperty(globalThis, 'document', { configurable: true, value: document });
  Object.defineProperty(globalThis, 'location', {
    configurable: true,
    value: { hash: '#/dialogs/customer-private-name?evaluation=secret-evaluation-id&text=private-transcript' },
  });
  try {
    for (const [locale, routeName] of [['ru', 'Диалоги'], ['kk', 'Диалогтар'], ['en', 'Conversations']]) {
      recorded.clear();
      updateMetadata(locale);
      assert.equal(document.title, `${routeName} · EVA — Expert Virtual Auditor`);
      assert.doesNotMatch([...recorded.values()].join(' '), /customer-private-name|secret-evaluation-id|private-transcript/);
      assert.equal(recorded.get('meta[property="og:title"]:content'), document.title);
      assert.equal(recorded.get('meta[property="og:locale"]:content'), expected[locale].language);
    }
  } finally {
    if (oldDocument) Object.defineProperty(globalThis, 'document', oldDocument); else delete globalThis.document;
    if (oldLocation) Object.defineProperty(globalThis, 'location', oldLocation); else delete globalThis.location;
  }
});

test('public origin accepts empty configuration and normalizes valid HTTPS origins', () => {
  assert.equal(validatePublicOrigin(''), '');
  assert.equal(validatePublicOrigin('   '), '');
  assert.equal(validatePublicOrigin('https://EVA.EXAMPLE:443/'), origin);
  assert.equal(validatePublicOrigin('https://audit.eva.example'), 'https://audit.eva.example');
});

test('public origin rejects credentials, non-HTTPS URLs, paths, queries and local addresses', () => {
  const invalid = [
    'not a url', '/about/en/', 'http://eva.example', 'ftp://eva.example',
    'javascript:alert(1)', 'data:text/html,test', 'https://user:password@eva.example',
    'https://user@eva.example', 'https://eva.example/path', 'https://eva.example/?token=secret',
    'https://eva.example/#overview', 'https://localhost', 'https://127.0.0.1',
    'https://10.0.0.1', 'https://[::1]', 'https://eva.local', 'https://intranet',
    'https://localhost.', 'https://eva.local.',
  ];
  for (const value of invalid) assert.throws(() => validatePublicOrigin(value), undefined, value);
});
