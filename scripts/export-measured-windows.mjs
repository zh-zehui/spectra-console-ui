import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
import { resolve } from 'node:path';

const input = resolve(process.argv[2] || 'qa');
const destination = resolve('dist/measured/data');
const task = 'task_agile_full_292630d126a1';
const views = [{ id: 'full', label: '全记录 · 0–1000 ms', start_ms: 0, end_ms: 1000, source: 'recorded-iq-full-stft-preview' }];
for (const start of [10, 500, 980, 990]) {
  const bytes = await readFile(resolve(input, `agile-window-${start}.json`));
  const value = JSON.parse(bytes.toString('utf8'));
  assert.equal(value.source, 'recorded-iq-window-stft');
  assert.equal(value.sample_start, start * 200000);
  assert.equal(value.sample_stop, (start + 10) * 200000);
  assert.equal(value.db.length, value.rows);
  assert.ok(value.db.every(row => row.length === value.columns && row.every(Number.isFinite)));
  const file = `window-${start}.json`;
  const record = {
    schema: 'spectra-recorded-window/v1', task_id: task, start_ms: start, end_ms: start + 10,
    source: value.source, sample_start: value.sample_start, sample_stop: value.sample_stop,
    source_response_sha256: createHash('sha256').update(bytes).digest('hex'),
    spectrogram: {
      rows: value.rows, columns: value.columns, db: value.db,
      low_mhz: value.low_mhz, high_mhz: value.high_mhz, min_db: value.min_db, max_db: value.max_db,
      fft_size: value.fft_size, time_order: 'earliest_row_first', pooling: 'maximum', power_calibrated: false
    }
  };
  await writeFile(resolve(destination, file), JSON.stringify(record) + '\n');
  views.push({ id: String(start), label: `${start}–${start + 10} ms`, start_ms: start, end_ms: start + 10, source: value.source, file });
}
await writeFile(resolve(destination, 'windows-index.json'), JSON.stringify({ task_id: task, default_view_id: '990', views }, null, 2) + '\n');
console.log('Exported four real IQ window matrices; no simulation or device writes.');
