import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';

// Inputs are private, locally downloaded GET responses. Only the explicit fields
// below enter the public archive; full reports, payloads and device IDs stay local.
const input = resolve(process.argv[2] || 'qa');
const output = resolve('dist/measured/data');
const definitions = [
  { id: 'task_batch24_dsh_31b06ee649b1', slug: 'wifi-ble-24', title: '2.4 GHz · Wi-Fi 与 BLE', description: '同一段实采 IQ 中并行查看 Wi-Fi MAC FCS 与 BLE CRC 证据，保留未解释的粗活动。', screenshot: 'measured-24ghz.png' },
  { id: 'task_batch_dsh_b02578c6283b', slug: 'wifi-58', title: '5.8 GHz · 宽带协议检查', description: '160 MHz 分析范围内的 Wi-Fi 校验帧与独立 PHY 报头证据。计划检查完成不代表所有信号均已识别。', screenshot: 'measured-58ghz.png' },
  { id: 'task_drone_dsh_5b1ddaa81022', slug: 'mixed-link', title: '5.8 GHz · Wi-Fi 与图传候选', description: '在已有 Wi-Fi 证据上追加 196 条样本特征匹配，保持两类证据独立；此任务与宽带协议案例复用同一段 IQ。', screenshot: 'measured-mixed-link.png' },
  { id: 'task_drone_third_368a63db4548', slug: 'link-candidate', title: '5.8 GHz · 20 MHz 图传候选', description: '另一段实采记录中的 ZC601/root252 特征匹配。190 条参考符号匹配仍不等于协议 CRC 或视频解码。', screenshot: 'measured-link-candidate.png' }
];
const parse = async path => JSON.parse((await readFile(path, 'utf8')).replace(/^\uFEFF/, ''));
const history = await parse(resolve(input, 'measured-task-history.json'));
await mkdir(output, { recursive: true });
const cases = [];
for (const definition of definitions) {
  const bytes = await readFile(resolve(input, `history-${definition.id}.json`));
  const value = JSON.parse(bytes.toString('utf8'));
  const report = value.report;
  const capture = report.captures[0];
  const metadata = capture.metadata;
  assert.equal(report.task_id, definition.id);
  assert.equal(report.data_origin, 'rf');
  assert.equal(metadata.kind, 'rf_capture');
  assert.equal(metadata.simulated, false);
  assert.equal(value.visual.db.length, value.visual.rows);
  for (const row of value.visual.db) {
    assert.equal(row.length, value.visual.columns);
    assert.ok(row.every(Number.isFinite));
  }
  const raw = value.raw_signals || value.signals;
  const counts = {
    detected_regions: value.event_count,
    wifi_frames: raw.filter(s => s.status === 'verified' && s.type === 'wifi').length,
    ble_frames: raw.filter(s => s.status === 'verified' && s.type === 'ble').length,
    phy_headers: raw.filter(s => s.status === 'header').length,
    signature_matches: raw.filter(s => s.status === 'signature').length
  };
  const lineage = report.recording_replay || report.continuation;
  const record = history.tasks.find(t => t.id === definition.id);
  const dataset = {
    schema: 'spectra-measured-task/v1',
    ...definition,
    task_id: definition.id,
    status: value.status,
    task_recorded_at_utc: record?.date || null,
    provenance: {
      data_origin: report.data_origin,
      simulated: metadata.simulated,
      capture_id: capture.artifact_id,
      source_task_id: lineage?.source_task_id || null,
      analysis_mode: report.recording_replay ? 'recorded_iq_reanalysis' : report.continuation ? 'continue_recorded_analysis' : 'rf_capture_analysis',
      new_rf_capture: lineage ? false : null,
      captured_at_utc: metadata.host_stream_command_utc,
      capture_sha256: lineage?.capture_sha256 || null,
      source_visual_sha256: createHash('sha256').update(bytes).digest('hex')
    },
    acquisition: {
      receiver_model: metadata.device_info?.mboard_id || null,
      center_mhz: metadata.center_hz / 1e6,
      analysis_low_mhz: value.frequency_scope.analysis_low_hz / 1e6,
      analysis_high_mhz: value.frequency_scope.analysis_high_hz / 1e6,
      analysis_bandwidth_mhz: value.bandwidth_mhz,
      sample_rate_msps: metadata.sample_rate_hz / 1e6,
      sample_count: metadata.sample_count,
      duration_ms: metadata.duration_s * 1000,
      complete: metadata.complete,
      overflow_count: metadata.overflow_count,
      gap_count: metadata.gaps?.length || 0,
      power_calibrated: false,
      frontend_passband_calibrated: value.frequency_scope.frontend_passband_calibrated
    },
    counts,
    protocol_coverage: {
      planned: report.protocol_coverage?.planned || false,
      complete: report.protocol_coverage?.complete || false,
      recognition_exhaustive: report.protocol_coverage?.recognition_exhaustive || false,
      plans: (report.protocol_coverage?.plans || []).map(p => ({ total_units: p.total_units, completed_units: p.completed_units, failed_units: p.failed_units }))
    },
    signature_profiles: (report.drone_video_evidence || []).map(e => ({
      evidence_id: e.evidence_id,
      profile_id: e.profile_id,
      center_mhz: e.center_hz / 1e6,
      matched_references: e.matched_references,
      estimated_repetition_ms: e.estimated_repetition_s == null ? null : e.estimated_repetition_s * 1000,
      evidence_level: e.evidence_level,
      protocol_confirmed: false,
      nominal_channel_bandwidth_mhz: e.profile?.nominal_channel_bandwidth_hz / 1e6,
      training_capture_id: e.profile?.training?.capture_id || null,
      training_interval_s: e.profile?.training?.interval_s || null,
      coverage_complete: e.coverage?.complete || false
    })),
    signals: raw.map(s => ({
      id: s.id,
      type: s.type,
      status: s.status,
      frequency_low_mhz: s.f0,
      frequency_high_mhz: s.f1,
      time_start_ms: s.t0 * value.duration_ms / 100,
      time_stop_ms: s.t1 * value.duration_ms / 100,
      ...(s.frame_subtype ? { frame_subtype: s.frame_subtype } : {}),
      ...(s.recognition_boundary_kind ? { boundary_kind: s.recognition_boundary_kind } : {})
    })),
    spectrogram: {
      rows: value.visual.rows,
      columns: value.visual.columns,
      low_mhz: value.visual.low_mhz,
      high_mhz: value.visual.high_mhz,
      min_db: value.visual.min_db,
      max_db: value.visual.max_db,
      fft_size: value.visual.fft_size,
      time_order: 'earliest_row_first',
      pooling: 'maximum',
      power_calibrated: false,
      db: value.visual.db
    },
    limitations: [
      'Counts are gateway-exported evidence observations, not device counts or measured recognition accuracy.',
      'PHY headers, integrity-checked frames and recording signature matches are separate evidence levels.',
      'Coarse activity regions are not packet boundaries. All remaining activity can remain unknown.',
      'The 192-row maximum-pooled STFT preview does not replace original IQ; power and timing are not independently calibrated.',
      'Signature profiles were fitted on short windows of the same recording; match counts include the training interval and are not independent test accuracy.',
      'The published extraction omits device serials, network addresses, decoded identifiers, payload bytes, local paths and credentials.'
    ]
  };
  await writeFile(resolve(output, `${definition.slug}.json`), JSON.stringify(dataset) + '\n');
  cases.push({ ...definition, task_id: definition.id, status: dataset.status, acquisition: dataset.acquisition, counts, provenance: dataset.provenance, dataset: `${definition.slug}.json` });
}
const index = {
  schema: 'spectra-measured-index/v1',
  exported_at_utc: new Date().toISOString(),
  source: 'GET /edge/tasks and GET /edge/tasks/{id}/visual from the existing Jetson gateway',
  history_scope: 'Tasks visible in the gateway history response at export time, not all runs or all past captures.',
  history_date_semantics: 'The gateway list date is a task record timestamp, not necessarily the RF capture timestamp.',
  unique_capture_count: new Set(cases.map(c => c.provenance.capture_id)).size,
  cases,
  history: history.tasks.map(t => ({ task_id: t.id, center_mhz: t.center, bandwidth_mhz: t.bandwidth, recorded_at_utc: t.date || null, status: t.status, case_slug: definitions.find(c => c.id === t.id)?.slug || null }))
};
await writeFile(resolve(output, 'index.json'), JSON.stringify(index, null, 2) + '\n');
console.log(`Exported ${cases.length} cases from ${index.unique_capture_count} recordings and ${index.history.length} visible task records.`);
