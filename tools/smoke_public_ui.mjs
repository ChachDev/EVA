// Drive a fresh visitor through the public audio history using a synthetic English WAV.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import { basename } from 'node:path';

const [origin, wav] = process.argv.slice(2);
if (!origin?.startsWith('https://') || !wav) throw Error('Supply the public HTTPS URL and a synthetic WAV.');
const { chromium } = createRequire(import.meta.url)(process.env.EVA_PLAYWRIGHT_PATH || 'playwright');
const browser = await chromium.launch({channel: process.env.EVA_BROWSER_CHANNEL || 'msedge', headless: true});
try {
  const page = await browser.newPage({locale:'ru-RU', viewport:{width:1440, height:1000}});
  const errors = [];
  page.on('pageerror', err => errors.push(err.message));
  const button = name => page.getByRole('button', {name, exact:true});
  await page.goto(origin + '/#/audio');
  await page.getByRole('heading',{name:'История распознавания'}).waitFor();
  await button('Загрузить аудиозапись').click();
  await page.getByLabel('Выбрать аудиофайл').setInputFiles(wav);
  await page.getByRole('dialog').getByRole('combobox').selectOption('EN');
  await button('Загрузить и распознать').click();
  await button('Скачать JSON').waitFor({timeout:150000});
  const jobUrl = page.url();
  await button('История распознавания').click();
  const historyDownload = page.waitForEvent('download');
  await page.getByRole('link',{name:'Скачать JSON'}).click();
  assert.ok((await historyDownload).suggestedFilename().endsWith('.json'));
  await page.getByRole('button',{name:new RegExp(basename(wav).replaceAll('.', '\\.'))}).click();
  assert.equal(page.url(),jobUrl);
  const downloadReady = page.waitForEvent('download');
  await button('Скачать JSON').click();
  const download = await downloadReady;
  const raw = await readFile(await download.path());
  const data = JSON.parse(raw);
  assert.equal(data.channel,'voice_call');
  assert.ok(data.messages.length && data.messages.every(m => typeof m.offset_seconds === 'number'));
  await button('Новый диалог из записи').click();
  assert.equal(await page.getByRole('dialog').getByLabel('Канал',{exact:true}).inputValue(), 'voice_call');
  assert.deepEqual(JSON.parse(await page.getByRole('dialog').locator('textarea').inputValue()),data);
  await button('Сохранить диалог').click();
  await page.waitForURL('**/#/dialogs/*');
  const firstUrl = page.url();
  await button('Новый диалог').click();
  await page.getByLabel('Выбрать файл переписки').setInputFiles({name:download.suggestedFilename(),mimeType:'application/json',buffer:raw});
  await page.waitForFunction(() => document.querySelector('dialog textarea')?.value.includes('voice_call'));
  await button('Сохранить диалог').click();
  await page.waitForURL(url => url.href !== firstUrl && url.hash.startsWith('#/dialogs/'));
  page.on('dialog',dialog => dialog.accept());
  await button('Удалить диалог').click();
  await page.waitForURL('**/#/dialogs');
  await button('Чек-листы').click();
  await button('Сохранить чек-лист').click();
  await button('Удалить версию').click();
  await button('Удалить версию').waitFor({state:'hidden'});
  assert.deepEqual(errors,[]);
  console.log('Public browser passed: history, download, direct reuse, file reimport, dialogue and checklist deletion.');
} finally {
  await browser.close();
}
