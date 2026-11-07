// Rehearse the reviewer workflow on synthetic batch case 03: public notification settings.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { writeFile } from 'node:fs/promises';
const { chromium } = createRequire(import.meta.url)(process.env.EVA_PLAYWRIGHT_PATH || 'playwright');
assert.equal(process.env.EVA_VERIFY_ISOLATED, '1');
const id = process.argv[2], api = 'http://127.0.0.1:18500/api/v1';
assert.match(id || '', /^[0-9a-f-]{36}$/);
const get = async p => {const r = await fetch(api + p); assert.equal(r.status, 200); return r.json();};
const [rec, snap] = await Promise.all([get('/evaluations/'+id), get('/evaluations/'+id+'/snapshot')]);
const chat = snap.conversation;
assert.match(chat.id, /^qa-local-[0-9a-f]{12}-03$/);
assert.equal(chat.messages[0].text, 'Where are the notification settings?');
assert.equal(rec.scoring.total, null);
assert.deepEqual(rec.original.criteria.filter(c => c.status === 'needs_review').map(c => c.criterion_id), ['identity']);
const criterion = snap.checklist.criteria.find(c => c.id === 'identity');
assert.equal(criterion.allow_na, true);
const ui = 'http://127.0.0.1:5186/#/dialogs/'+chat.id+'?evaluation='+id;
const browser = await chromium.launch({channel:process.env.EVA_BROWSER_CHANNEL || 'msedge',headless:true});
try {
  const page = await browser.newPage();
  await page.goto(ui);
  const row = page.locator('.criterion').filter({has:page.getByText(criterion.title,{exact:true})});
  const toggle = row.locator('.criterion-toggle');
  await toggle.waitFor();
  if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
  await row.getByRole('button',{name:'Оспорить',exact:true}).click();
  const form = page.getByRole('dialog'), reason = 'Учебное рассмотрение needs_review: '+id;
  const save = async path => {
    const [r] = await Promise.all([page.waitForResponse(r => r.url() === api+path && r.request().method() === 'POST'),
      form.getByRole('button',{name:'Сохранить',exact:true}).click()]);
    assert.equal(r.status(),201);
    return r.json();
  };
  await form.getByLabel('Причина',{exact:true}).fill(reason);
  await form.getByRole('checkbox').nth(0).check();
  const opened = await save('/evaluations/'+id+'/appeals');
  const open = opened.appeals.find(a => a.status === 'open' && a.criterion_id === 'identity');
  assert.ok(open);
  await form.waitFor({state:'hidden'});
  await page.getByRole('navigation').getByRole('button',{name:/Апелляции/}).click();
  const card = page.locator('article.live-appeal').filter({hasText:reason});
  await card.getByRole('button',{name:'Рассмотреть',exact:true}).click();
  await form.getByRole('combobox',{name:/^Решение/}).selectOption('accepted');
  await form.getByRole('combobox',{name:/^Новое значение/}).selectOption('na');
  await form.getByLabel('Причина',{exact:true}).fill('Учебное решение проверяющего: клиент спрашивает, где настройки уведомлений. Раскрытия личных данных и действия по счёту нет.');
  await form.getByRole('checkbox').nth(0).check();
  const result = await save('/appeals/'+open.id+'/decisions');
  assert.equal(result.original.scoring.total,null);
  assert.deepEqual(result.original.original,rec.original);
  assert.equal(typeof result.final.scoring.total,'number');
  await page.goto(ui);
  await page.locator('.review-banner').waitFor();
  assert.equal(await page.locator('.gauge strong').innerText(),result.final.scoring.total.toLocaleString('ru-RU',{maximumFractionDigits:2}));
  const receipt = {evaluation:id,criterion:'identity',original_total:null,manual_final_total:result.final.scoring.total,
    original_unchanged:true,checked_after_reopen:true,synthetic:true,manual_evidence:chat.messages[0].id};
  await writeFile(new URL('../data/live-project-check/manual-'+id+'.json',import.meta.url),JSON.stringify(receipt,null,2)+'\n');
  console.log(receipt);
} finally {await browser.close();}
