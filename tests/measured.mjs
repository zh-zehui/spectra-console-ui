import assert from 'node:assert/strict';
import { readFile, access } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { chromium } from 'playwright';
import { createServer } from '../scripts/serve.mjs';

const index = JSON.parse(await readFile(new URL('../dist/measured/data/index.json', import.meta.url), 'utf8'));
const taskID = 'task_agile_full_292630d126a1';
assert.equal(index.history.length, 1);
assert.equal(index.history[0].task_id, taskID);
assert.equal(index.cases.length, 1);
assert.equal(index.cases[0].task_id, taskID);
assert.equal(index.unique_capture_count, 1);
const privateKeys = new Set(['payload', 'payload_hex', 'mac_address', 'device_info', 'mboard_serial', 'module_serial', 'api_key', 'password', 'iq_path', 'path', 'report']);
function assertPublicFields(value) {
  if (!value || typeof value !== 'object') return;
  for (const [key, child] of Object.entries(value)) { assert.ok(!privateKeys.has(key), `Private field ${key}`); assertPublicFields(child); }
}
const datasets = new Map();
for (const item of index.cases) {
  const value = JSON.parse(await readFile(new URL('../dist/measured/data/' + item.dataset, import.meta.url), 'utf8'));
  assertPublicFields(value);
  assert.equal(value.provenance.simulated, false);
  assert.equal(value.provenance.data_origin, 'rf');
  assert.equal(value.acquisition.complete, true);
  assert.equal(value.spectrogram.time_order, 'earliest_row_first');
  assert.equal(value.spectrogram.rows, value.spectrogram.db.length);
  assert.ok(value.spectrogram.db.every(row => row.length === value.spectrogram.columns && row.every(Number.isFinite)));
  assert.equal(value.counts.wifi_frames, value.signals.filter(s => s.status === 'verified' && s.type === 'wifi').length);
  assert.equal(value.counts.ble_frames, value.signals.filter(s => s.status === 'verified' && s.type === 'ble').length);
  assert.equal(value.counts.phy_headers, value.signals.filter(s => s.status === 'header').length);
  assert.equal(value.counts.signature_matches, value.signals.filter(s => s.status === 'signature').length);
  assert.ok(value.signals.every(s => [s.time_start_ms, s.time_stop_ms, s.frequency_low_mhz, s.frequency_high_mhz].every(Number.isFinite) && s.time_start_ms <= s.time_stop_ms && s.frequency_low_mhz <= s.frequency_high_mhz));
  await access(new URL('../dist/measured/images/' + value.screenshot, import.meta.url));
  datasets.set(value.slug, value);
}
const task = datasets.get('agile-full');
assert.equal(task.counts.wifi_frames, 5767);
assert.equal(task.counts.phy_headers, 492);
assert.equal(task.counts.signature_matches, 395);
assert.equal(task.counts.detected_regions, 1683);
assert.equal(task.acquisition.duration_ms, 1000);
assert.equal(task.acquisition.sample_count, 200000000);
assert.equal(task.protocol_coverage.plans[0].completed_units, 70);
assert.equal(task.hopping_evidence[0].coverage.completed_units, 100);
assert.equal(task.hopping_evidence[0].coverage.total_units, 100);
assert.equal(task.hopping_evidence[0].sequence.transitions.length, 2);
assert.equal(task.hopping_evidence[0].sequence.multiple_bandwidth_candidates, false);
assert.ok(task.signals.some(s => s.status === 'signature' && s.time_start_ms > 990 && (s.frequency_low_mhz + s.frequency_high_mhz) / 2 === 5755.5));
const screenshotRecords = JSON.parse(await readFile(new URL('../dist/measured/images/screenshot-records.json', import.meta.url), 'utf8'));
assert.equal(screenshotRecords.task_id, taskID);
assert.deepEqual(screenshotRecords.snapshots.map(s => s.start_ms), [10, 500, 980, 990]);
assert.equal(screenshotRecords.writes, 0);
assert.deepEqual(screenshotRecords.errors, []);
for (const snapshot of screenshotRecords.snapshots) {
  const count = signals => signals.reduce((out, s) => { out[s.status] = (out[s.status] || 0) + 1; return out; }, {});
  const visible = task.signals.filter(s => s.time_start_ms < snapshot.end_ms && s.time_stop_ms > snapshot.start_ms && s.frequency_high_mhz > task.acquisition.analysis_low_mhz && s.frequency_low_mhz < task.acquisition.analysis_high_mhz);
  assert.deepEqual(snapshot.visible_counts, count(visible));
  assert.deepEqual(snapshot.enabled_counts, count(task.signals));
  assert.ok(snapshot.layers.length === 4 && snapshot.layers.every(layer => layer.enabled));
  assert.equal(snapshot.all_visible_ids_included, true);
  assert.equal(snapshot.stft.sample_start, snapshot.start_ms * 200000);
  assert.equal(snapshot.stft.sample_stop, snapshot.end_ms * 200000);
  await access(new URL('../dist/measured/images/' + snapshot.image, import.meta.url));
}

const server = createServer();
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const edge = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
let browser;
try {
  browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' && existsSync(edge) ? { executablePath: edge } : {}) });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
  const errors = [], writes = [], hardware = [], failedAssets = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', request => { if (request.method() !== 'GET') writes.push(request.url()); if (request.url().includes('/edge/')) hardware.push(request.url()); });
  page.on('response', response => { if (!response.ok() && response.url().startsWith(base)) failedAssets.push(response.url()); });
  await page.goto(base + '/measured/', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => document.querySelector('#case-detail').getAttribute('aria-busy') === 'false');
  assert.equal(await page.locator('#history-rows tr').count(), 1);
  assert.equal(await page.locator('[data-case]').count(), 1);
  assert.match(await page.locator('#case-id').innerText(), new RegExp(taskID));
  assert.match(await page.locator('#evidence-counts').innerText(), /5,767/);
  assert.match(await page.locator('#coverage-note').innerText(), /100\/100/);
  assert.ok(await page.locator('#spectrogram').evaluate(canvas => { const d = canvas.getContext('2d').getImageData(100, 100, 1, 1).data; return d[3] === 255; }));
  const box = await page.locator('#spectrogram').boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  assert.match(await page.locator('#chart-readout').innerText(), /预览格/);
  for (const item of index.cases) {
    await page.locator(`[data-case="${item.slug}"]`).click();
    await page.waitForFunction(id => document.querySelector('#case-id').textContent.startsWith(id) && document.querySelector('#case-detail').getAttribute('aria-busy') === 'false', item.task_id);
    assert.equal(await page.locator('#download-data').getAttribute('href'), 'data/' + item.dataset);
    for (const filter of ['verified', 'header', 'signature', 'unknown']) {
      await page.locator(`[data-filter="${filter}"]`).click();
      const count = datasets.get(item.slug).signals.filter(s => s.status === filter).length;
      assert.equal(await page.locator('#evidence-rows [data-signal]').count(), Math.min(12, count));
    }
  }
  await page.locator('[data-filter="verified"]').click();
  assert.equal(await page.locator('#evidence-rows [data-signal]').count(), 12);
  await page.locator('#history-filter').selectOption('partial');
  assert.equal(await page.locator('#history-rows tr').count(), 0);
  assert.equal(await page.locator('#history-rows .case-open').count(), 0);
  await page.locator('#history-filter').selectOption('all');
  await page.locator('[data-case="agile-full"]').click();
  await page.waitForFunction(() => document.querySelector('#case-title').textContent.includes('全时段') && document.querySelector('#case-detail').getAttribute('aria-busy') === 'false');
  await page.locator('[data-filter="signature"]').click();
  await page.locator('#overlay').uncheck();
  await page.locator('#overlay').check();
  await page.locator('#original-image').scrollIntoViewIfNeeded();
  await page.waitForFunction(() => document.querySelector('#original-image').complete && document.querySelector('#original-image').naturalWidth > 0);
  assert.equal(await page.locator('.original-ui').count(), 4);
  for (const panel of await page.locator('.original-ui').all()) {
    await panel.evaluate(element => { element.open = true; });
    await panel.locator('img').scrollIntoViewIfNeeded();
    await panel.locator('img').evaluate(async image => { await image.decode(); });
    await panel.evaluate(element => { element.open = false; });
  }
  await page.locator('.original-ui').first().evaluate(element => { element.open = true; });
  await page.evaluate(() => window.scrollTo(0, 0));
  if (process.argv.includes('--screenshots')) await page.screenshot({ path: 'docs/screenshots/measured-archive.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2), false);
  assert.deepEqual(errors, []); assert.deepEqual(writes, []); assert.deepEqual(hardware, []); assert.deepEqual(failedAssets, []);
  console.log('PASS: only task_agile_full_292630d126a1 is published; provenance, counts, coverage, transitions, STFT, image, filters and mobile; zero device requests or writes');
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
