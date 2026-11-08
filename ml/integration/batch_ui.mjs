// Against an isolated backend seeded with the synthetic-demo group only.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const {chromium}=createRequire(import.meta.url)(process.env.EVA_PLAYWRIGHT_PATH||'playwright');
const url=process.argv[2];
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(url))throw Error('Expected isolated loopback UI');
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
  const page=await browser.newPage(); const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url+'/#/batches');
  await page.getByLabel('Импортированный набор').selectOption('synthetic-demo');
  await page.getByRole('button',{name:'Оценить весь набор'}).click();
  await page.getByRole('button',{name:'Разметить вслепую'}).waitFor();
  await page.reload();
  await page.getByRole('button',{name:'Разметить вслепую'}).click();
  await page.getByLabel('Разметчик',{exact:true}).fill('synthetic-browser');
  const fields=page.locator('section.live-panel select');
  assert.equal(await fields.count(),8);
  for(const field of await fields.all())await field.selectOption({index:1});
  assert.equal(await page.getByText('Сравнение с первоначальным ответом модели').count(),0);
  await page.getByRole('button',{name:'Сохранить разметку'}).click();
  await page.getByRole('heading',{name:'Сравнение с первоначальным ответом модели'}).waitFor();
  await page.getByRole('button',{name:'К набору'}).click();
  await page.getByRole('button',{name:'Качество по разметке'}).click();
  await page.getByRole('heading',{name:'Размечено диалогов: 1 из 1'}).waitFor();
  assert.deepEqual(errors,[]);console.log('PASS: launch, persisted progress on reload, blind submission and quality view');
}finally{await browser.close()}
