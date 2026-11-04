import assert from 'node:assert/strict';
import { mkdir, readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { chromium } from 'playwright';
import { createServer } from '../scripts/serve.mjs';

const screenshots = process.argv.includes('--screenshots');
const server = createServer();
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const edge = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
let browser;
try {
  browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' && existsSync(edge) ? { executablePath: edge } : {}) });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1100 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  // Keep screenshots and tests repeatable without external font services.
  await page.route('https://fonts.googleapis.com/**', route => route.abort());
  await page.goto(base, { waitUntil: 'networkidle' });
  await page.locator('#signal-rows tr').first().waitFor();
  assert.equal(await page.locator('#signal-rows tr').count(), 6);
  assert.match(await page.locator('.demo-badge').innerText(), /示例/);
  if (screenshots) {
    await mkdir('docs/screenshots', { recursive: true });
    await page.screenshot({ path: 'docs/screenshots/workbench.png', fullPage: true });
  }
  await page.locator('#stage-switch [data-stage="0"]').click();
  if (screenshots) await page.locator('.analysis-layout').screenshot({ path: 'docs/screenshots/initial-segmentation.png' });
  await page.locator('#stage-switch [data-stage="2"]').click();
  await page.locator('[data-signal="s4"]').click();
  assert.match(await page.locator('.evidence-title').innerText(), /未知/);
  if (screenshots) await page.locator('.analysis-layout').screenshot({ path: 'docs/screenshots/evidence.png' });
  await page.locator('[data-filter="verified"]').click();
  assert.equal(await page.locator('#signal-rows tr').count(), 3);
  await page.locator('[data-filter="all"]').click();
  const waterfall = page.locator('#waterfall');
  const box = await waterfall.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -350);
  await page.locator('#reset-zoom').click();
  await page.locator('.preset-buttons [data-preset="58"]').click();
  assert.match(await page.locator('#range-label').innerText(), /5750/);
  assert.equal(await page.locator('#signal-rows tr').count(), 5);
  await page.locator('.preset-buttons [data-preset="24"]').click();
  await page.locator('#run-task').click();
  await page.waitForFunction(() => document.querySelector('#result-status').textContent.includes('完成'));
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#export-report').click();
  const download = await downloadPromise;
  const report = JSON.parse(await readFile(await download.path(), 'utf8'));
  assert.equal(report.kind, 'INTERACTIVE-DEMO-NOT-RF');
  assert.equal(report.signals.length, 6);
  await page.locator('[data-view="history"]').click();
  assert.ok(await page.locator('.history-card').count() > 0);
  await page.locator('[data-view="tools"]').click();
  assert.equal(await page.locator('.tool-card').count(), 6);
  await page.locator('[data-view="workbench"]').click();
  await page.locator('#presentation').click();
  assert.ok(await page.locator('body').evaluate(el => el.classList.contains('presentation')));
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('body').evaluate(el => el.classList.contains('presentation')), false);
  await page.locator('#help').click();
  await page.locator('.dialog-confirm').click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2), false);
  if (screenshots) await page.screenshot({ path: 'docs/screenshots/mobile.png', fullPage: true });
  assert.deepEqual(errors, []);
  console.log('PASS: demo, evidence, filters, chart, presets, task, export, history, tools, presentation, help and mobile layout');
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
