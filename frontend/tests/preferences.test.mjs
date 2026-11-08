import assert from 'node:assert/strict';
import test, { afterEach } from 'node:test';
import {
  detectLocale, formatDate, formatNumber, getLocale, isLocale,
  messages, setLocale, subscribeLocale, translate,
} from '../src/i18n-core.ts';

afterEach(() => setLocale('ru', false));

test('saved interface language wins over browser preferences', () => {
  assert.equal(detectLocale('kk', ['en-US', 'ru-RU']), 'kk');
  assert.equal(detectLocale('ru', ['en-GB']), 'ru');
  assert.equal(detectLocale('en', ['kk-KZ']), 'en');
});

test('invalid saved languages use the first supported browser language', () => {
  for (const saved of [null, undefined, '', 'de', 'KZ', 'ru-RU', {}, 42]) {
    assert.equal(detectLocale(saved, ['de-DE', 'KK-kz', 'en-US']), 'kk');
  }
  assert.equal(detectLocale(null, ['kz-KZ']), 'kk');
  assert.equal(detectLocale(null, ['en-US', 'ru-RU']), 'en');
  assert.equal(detectLocale(null, ['fr-FR']), 'ru');
  assert.equal(detectLocale(null), 'ru');
  assert.equal(isLocale('kk'), true);
  assert.equal(isLocale('kz'), false);
  assert.equal(isLocale(null), false);
});

test('Russian source copy and exact Kazakh and English translations are available', () => {
  assert.equal(translate('Сохранить', {}, 'ru'), 'Сохранить');
  assert.equal(translate('Сохранить', {}, 'kk'), 'Сақтау');
  assert.equal(translate('Сохранить', {}, 'en'), 'Save');
  assert.equal(translate('Критерий {number}', { number: 3 }, 'kk'), '3-критерий');
  assert.equal(translate('Критерий {number}', { number: 3 }, 'en'), 'Criterion 3');
});

test('translation interpolates values without translating or interpreting user content', () => {
  const reason = 'Клиент сказал: <script>{revision}</script> $&';
  assert.equal(translate('Причина обращения: {reason}', { reason }, 'en'), `Appeal reason: ${reason}`);
  assert.equal(translate('Файл сохранён. ID: {id}', { id: 'asset-{unknown}' }, 'kk'), 'Файл сақталды. ID: asset-{unknown}');
  assert.equal(translate('Реплик: {count}', { count: 0 }, 'en'), 'Messages: 0');
  assert.equal(translate('Критерий {number}', {}, 'en'), 'Criterion {number}');
});

test('unknown text stays readable and inherited parameter properties are not interpolated', () => {
  assert.equal(translate('Unmapped server response', {}, 'kk'), 'Unmapped server response');
  assert.equal(translate('Unknown {id}', { id: '42' }, 'en'), 'Unknown 42');
  const params = Object.create({ number: 99 });
  assert.equal(translate('Критерий {number}', params, 'en'), 'Criterion {number}');
});

test('every dictionary entry supplies nonempty Kazakh and English copy with matching placeholders', () => {
  const placeholders = value => [...value.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort();
  assert.ok(Object.keys(messages).length > 0);
  for (const [source, translations] of Object.entries(messages)) {
    for (const locale of ['kk', 'en']) {
      assert.equal(typeof translations[locale], 'string', `${locale}: ${source}`);
      assert.ok(translations[locale].trim(), `${locale}: empty translation for ${source}`);
      assert.deepEqual(placeholders(translations[locale]), placeholders(source), `${locale}: parameters for ${source}`);
    }
  }
});

test('changing the active language updates translation and number formatting together', () => {
  setLocale('en', false);
  assert.equal(getLocale(), 'en');
  assert.equal(translate('Сохранить'), 'Save');
  assert.equal(formatNumber(12345.678), '12,345.68');
  setLocale('kk', false);
  assert.equal(translate('Сохранить'), 'Сақтау');
  assert.equal(formatNumber(12345.678).replace(/[\u00a0\u202f]/g, ' '), '12 345,68');
  setLocale('ru', false);
  assert.equal(formatNumber(12345.678).replace(/[\u00a0\u202f]/g, ' '), '12 345,68');
});

test('number formatting preserves missing scores and honors requested precision', () => {
  assert.equal(formatNumber(null, {}, 'en'), '—');
  assert.equal(formatNumber(undefined, {}, 'kk'), '—');
  assert.equal(formatNumber(0, {}, 'ru'), '0');
  assert.equal(formatNumber(1.236, { minimumFractionDigits: 3, maximumFractionDigits: 3 }, 'en'), '1.236');
  assert.equal(formatNumber(0.42, { style: 'percent' }, 'en'), '42%');
});

test('date formatting respects language and explicit timezone and preserves invalid source values', () => {
  const instant = '2026-11-08T04:05:00Z';
  assert.equal(formatDate(instant, { timeZone: 'UTC' }, 'en'), '08/11/2026, 04:05');
  assert.equal(formatDate(instant, { timeZone: 'UTC' }, 'ru'), '08.11.2026, 04:05');
  assert.equal(formatDate(instant, { timeZone: 'UTC' }, 'kk'), '08.11.26, 04:05');
  assert.equal(formatDate(instant, { timeZone: 'UTC', dateStyle: 'long', timeStyle: undefined }, 'en'), '8 November 2026');
  assert.equal(formatDate('not-a-date', {}, 'kk'), 'not-a-date');
});

test('date formatting accepts component options without conflicting default styles', () => {
  assert.equal(formatDate('2026-11-08T04:05:00Z', {
    timeZone: 'UTC', year: 'numeric', month: 'long', day: 'numeric',
  }, 'en'), '8 November 2026');
});

test('subscribers see the updated locale, invalid changes are ignored and unsubscribe works', () => {
  const observed = [];
  const unsubscribe = subscribeLocale(() => observed.push(getLocale()));
  try {
    setLocale('en', false);
    setLocale('invalid', false);
    assert.equal(getLocale(), 'en');
    assert.deepEqual(observed, ['en']);
    setLocale('kk', false);
    assert.deepEqual(observed, ['en', 'kk']);
    unsubscribe();
    setLocale('ru', false);
    assert.deepEqual(observed, ['en', 'kk']);
  } finally {
    unsubscribe();
  }
});
