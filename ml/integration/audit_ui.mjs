// Synthetic EVA audit_stack only. Set EVA_PLAYWRIGHT_PATH to an installed Playwright.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { createRequire } from 'node:module';
const {chromium}=createRequire(import.meta.url)(process.env.EVA_PLAYWRIGHT_PATH || 'playwright');
const {conversation,evaluation}=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
const browser=await chromium.launch({channel:process.env.EVA_BROWSER_CHANNEL || 'msedge',headless:true});
try {
  const page=await browser.newPage();
  await page.goto('http://127.0.0.1:5188/#/dialogs/'+conversation+'?evaluation='+evaluation);
  await page.getByRole('button',{name:'EVA Review · история и экспорт'}).click();
  await page.getByRole('heading',{name:'EVA Sentinel · нарушения'}).waitFor();
  const proof=page.locator('.criterion-detail .evidence-link').first();
  await proof.click();
  assert.ok(await page.locator('article.message.highlighted').count());
  const download=page.waitForEvent('download');
  await page.getByRole('button',{name:'Скачать JSON с историей и доказательствами'}).click();
  assert.match((await download).suggestedFilename(),/^eva-review-/);
  assert.ok(await page.getByText('Первоначальный балл',{exact:false}).count());
  console.log('PASS: live criterion evidence, source navigation, reviewer history, JSON export');
} finally {await browser.close();}
