'use strict';
const $ = selector => document.querySelector(selector);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = { index: null, current: null, filter: 'verified', rows: [], selected: null, loadId: 0 };
const colors = { verified: '#6cd8bf', header: '#b29ae3', signature: '#f092d1', unknown: '#e3ba76' };
const category = { verified: '完整性校验', header: '仅 PHY 报头', signature: '参考特征匹配', unknown: '粗活动 · 未确认' };
const date = value => value ? value.replace('T', ' ').replace(/\.\d+(?:\+00:00|Z)$/, ' UTC').replace(/(?:\+00:00|Z)$/, ' UTC') : '未记录';
const pair = (label, value) => `<div><dt>${escapeHTML(label)}</dt><dd>${escapeHTML(value)}</dd></div>`;
const format = value => Number(value).toLocaleString('en-US');

async function readJSON(path) {
  const response = await fetch(path, { cache: 'no-store' });
  if (!response.ok) throw Error(`归档读取失败 (${response.status})`);
  return response.json();
}

function renderIndex() {
  const index = state.index;
  $('#case-count').textContent = index.cases.length;
  const task = index.cases[0];
  $('#archive-stats').innerHTML = [[index.history.length, '个指定历史任务'], [task.acquisition.duration_ms + ' ms', '实采记录时长'], [format(task.counts.wifi_frames), '条 Wi-Fi 校验帧'], [task.counts.signature_matches, '条参考特征候选']].map(([n, label]) => `<div class="stat"><strong>${n}</strong><span>${label}</span></div>`).join('');
  $('#case-list').innerHTML = index.cases.map(c => `<button class="case-button" data-case="${escapeHTML(c.slug)}"><strong>${escapeHTML(c.title)}</strong><span>${c.acquisition.analysis_bandwidth_mhz} MHz · ${c.acquisition.duration_ms} ms · ${c.acquisition.sample_rate_msps} MS/s</span><small>实采 IQ · 历史分析 ↗</small></button>`).join('');
  document.querySelectorAll('[data-case]').forEach(button => button.onclick = () => { location.hash = button.dataset.case; });
  $('#exported-at').textContent = '记录导出于 ' + date(index.exported_at_utc);
  renderHistory();
}

function renderHistory() {
  const filter = $('#history-filter').value;
  const rows = state.index.history.filter(t => filter === 'all' || t.status === filter);
  $('#history-rows').innerHTML = rows.map(t => `<tr><td>${escapeHTML(t.task_id)}</td><td>${t.center_mhz} / ${t.bandwidth_mhz} MHz</td><td>${escapeHTML(date(t.recorded_at_utc))}</td><td><span class="status ${t.status === 'partial' ? 'partial' : ''}">${escapeHTML(t.status)}</span></td><td>${t.case_slug ? `<button class="case-open" data-open="${escapeHTML(t.case_slug)}">查看实采 ↗</button>` : '仅记录摘要'}</td></tr>`).join('');
  $('#history-summary').textContent = `显示 ${rows.length} / ${state.index.history.length} 条；本页仅公开指定任务 task_agile_full_292630d126a1。`;
  document.querySelectorAll('[data-open]').forEach(button => button.onclick = () => { location.hash = button.dataset.open; $('#case-detail').scrollIntoView({ behavior: 'smooth' }); });
}

async function loadCase() {
  const id = ++state.loadId;
  const slug = location.hash.slice(1);
  const definition = state.index.cases.find(c => c.slug === slug) || state.index.cases[0];
  $('#case-detail').setAttribute('aria-busy', 'true');
  const current = await readJSON('data/' + definition.dataset);
  if (id !== state.loadId) return;
  state.current = current;
  state.filter = current.default_filter || (current.counts.wifi_frames + current.counts.ble_frames ? 'verified' : 'signature');
  state.selected = null;
  document.querySelectorAll('[data-case]').forEach(button => { const active = button.dataset.case === current.slug; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active)); });
  $('#case-kind').textContent = current.provenance.analysis_mode === 'continue_recorded_analysis' ? 'RECORDED IQ · CONTINUED ANALYSIS' : 'RECORDED IQ · REANALYSIS';
  $('#case-title').textContent = current.title;
  $('#case-description').textContent = current.description;
  $('#case-id').textContent = current.task_id + ' / ' + current.status;
  $('#download-data').href = 'data/' + definition.dataset;
  $('#download-data').download = definition.dataset;
  const a = current.acquisition;
  $('#parameters').innerHTML = pair('分析范围 / MHz', `${a.analysis_low_mhz}–${a.analysis_high_mhz}`) + pair('采样率 / MS/s', a.sample_rate_msps) + pair('记录时长 / ms', a.duration_ms) + pair('采集复样本', format(a.sample_count));
  $('#chart-resolution').textContent = `${current.spectrogram.rows} × ${current.spectrogram.columns} · 最大池化`;
  $('#chart-range').textContent = `${a.analysis_bandwidth_mhz} MHz 分析范围 · 全部 ${a.duration_ms} ms 记录`;
  const counts = current.counts;
  $('#evidence-counts').innerHTML = [[counts.wifi_frames + counts.ble_frames, `校验帧 · Wi-Fi ${counts.wifi_frames} / BLE ${counts.ble_frames}`], [counts.phy_headers, '独立 PHY 报头'], [counts.signature_matches, '特征候选 · 非协议 CRC'], [counts.detected_regions, '粗活动区域 · 非设备数']].map(([n, label]) => `<div>${escapeHTML(label)}<strong>${format(n)}</strong></div>`).join('');
  const p = current.provenance;
  $('#provenance').innerHTML = pair('原始采集时间 / UTC', date(p.captured_at_utc)) + pair('任务记录时间 / UTC', date(current.task_recorded_at_utc)) + pair('采集记录 ID', p.capture_id) + pair('来源任务 ID', p.source_task_id) + pair('接收机 / 采集完整性', `${a.receiver_model} · complete=${a.complete} · overflows=${a.overflow_count} · gaps=${a.gap_count}`) + pair('分析方式', p.analysis_mode === 'continue_recorded_analysis' ? '延续已有分析；复用实采 IQ' : '复用实采 IQ 重新分析') + pair('原 IQ SHA-256', p.capture_sha256 || '未记录');
  const plans = current.protocol_coverage.plans;
  const hopping = current.hopping_evidence || [];
  $('#coverage-note').textContent = `协议计划检查：${plans.map(plan => `${plan.completed_units}/${plan.total_units} 项，失败 ${plan.failed_units}`).join('；')}。图传候选检查：${hopping.map(e => `${e.coverage.completed_units}/${e.coverage.total_units} 个时间单元，${format(e.coverage.reference_checks)} 次参考检查`).join('；')}。完整检查仅指声明的窗口/候选频点与配置，不表示全部信号已识别，也不确认发射机身份。`;
  $('#original-image').src = 'images/' + current.screenshot;
  $('#original-link').href = 'images/' + current.screenshot;
  renderEvidence();
  $('#case-detail').setAttribute('aria-busy', 'false');
}

function renderEvidence() {
  const current = state.current;
  const all = current.signals.filter(signal => signal.status === state.filter);
  state.rows = all.slice(0, 12);
  if (!state.rows.some(signal => signal.id === state.selected)) state.selected = state.rows[0]?.id || null;
  document.querySelectorAll('[data-filter]').forEach(button => { const active = button.dataset.filter === state.filter; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active)); });
  const notes = { verified: 'Wi-Fi MAC FCS / BLE CRC 校验通过的帧观测。帧位置为未独立标定的同步估计，验证通带不等于实测占用带宽。', header: '仅表示 PHY 报头校验结果，不能计作 MAC FCS 通过或完整载荷解码。', signature: '参考符号 / ZC 特征匹配。样本归属依赖现场信息，未完成协议 CRC、载荷或视频解码。', unknown: '能量检测形成的粗活动包络，可能包含多个重叠信号，未确认为单一协议或设备。' };
  $('#evidence-note').textContent = notes[state.filter];
  $('#evidence-rows').innerHTML = state.rows.map(signal => `<tr data-signal="${escapeHTML(signal.id)}" tabindex="0" class="${signal.id === state.selected ? 'selected' : ''}"><td style="color:${colors[signal.status]}">${escapeHTML(signal.frame_subtype || ({wifi:'Wi-Fi',ble:'BLE',drone:'图传候选'}[signal.type]) || category[signal.status])}</td><td>${signal.frequency_low_mhz.toFixed(2)}–${signal.frequency_high_mhz.toFixed(2)}</td><td>${signal.time_start_ms.toFixed(5)}–${signal.time_stop_ms.toFixed(5)}</td><td>${category[signal.status]}</td></tr>`).join('') || '<tr><td colspan="4">此任务未记录该类证据。</td></tr>';
  $('#evidence-page').textContent = `当前展示 ${state.rows.length} / ${all.length} 条${category[state.filter]}记录；完整条目见本案 JSON。图中仅叠加当前页，短时证据以细线提示。`;
  document.querySelectorAll('[data-signal]').forEach(row => {
    row.onclick = () => { state.selected = row.dataset.signal; renderEvidence(); };
    row.onkeydown = event => { if (event.key === 'Enter') row.click(); };
  });
  draw();
}

const geometry = () => { const bounds = $('#spectrogram').getBoundingClientRect(); return { width: bounds.width, height: bounds.height, left: 48, top: 14, plotWidth: bounds.width - 66, plotHeight: bounds.height - 52 }; };
function draw() {
  if (!state.current) return;
  const canvas = $('#spectrogram');
  const g = geometry();
  const ratio = Math.min(devicePixelRatio || 1, 2);
  canvas.width = Math.round(g.width * ratio); canvas.height = Math.round(g.height * ratio);
  const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio);
  const current = state.current, spec = current.spectrogram, a = current.acquisition;
  const raster = document.createElement('canvas'); raster.width = spec.columns; raster.height = spec.rows;
  const rasterCtx = raster.getContext('2d'), image = rasterCtx.createImageData(spec.columns, spec.rows);
  const stops = [[12,28,23],[30,80,54],[86,134,73],[170,195,112],[236,218,155]];
  for (let y = 0; y < spec.rows; y++) for (let x = 0; x < spec.columns; x++) {
    const fraction = Math.max(0, Math.min(1, (spec.db[y][x] - spec.min_db) / (spec.max_db - spec.min_db)));
    const value = fraction * (stops.length - 1), segment = Math.min(stops.length - 2, Math.floor(value)), mix = value - segment;
    const pixel = ((spec.rows - 1 - y) * spec.columns + x) * 4;
    for (let channel = 0; channel < 3; channel++) image.data[pixel + channel] = stops[segment][channel] * (1 - mix) + stops[segment + 1][channel] * mix;
    image.data[pixel + 3] = 255;
  }
  rasterCtx.putImageData(image, 0, 0);
  const sourceX = (a.analysis_low_mhz - spec.low_mhz) / (spec.high_mhz - spec.low_mhz) * spec.columns;
  const sourceWidth = a.analysis_bandwidth_mhz / (spec.high_mhz - spec.low_mhz) * spec.columns;
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(raster, sourceX, 0, sourceWidth, spec.rows, g.left, g.top, g.plotWidth, g.plotHeight);
  ctx.strokeStyle = 'rgba(200,220,200,.09)'; ctx.fillStyle = '#9cad9c'; ctx.font = '9px Consolas,monospace';
  for (let i = 0; i <= 4; i++) {
    const y = g.top + g.plotHeight * i / 4;
    ctx.beginPath(); ctx.moveTo(g.left, y); ctx.lineTo(g.left + g.plotWidth, y); ctx.stroke();
    ctx.textAlign = 'right'; ctx.fillText((a.duration_ms * (1 - i / 4)).toFixed(0), g.left - 7, y + 3);
    const x = g.left + g.plotWidth * i / 4;
    ctx.textAlign = 'center'; ctx.fillText((a.analysis_low_mhz + a.analysis_bandwidth_mhz * i / 4).toFixed(0), x, g.top + g.plotHeight + 18);
  }
  ctx.textAlign = 'left'; ctx.fillText('ms', 12, 10); ctx.textAlign = 'right'; ctx.fillText('MHz', g.width - 8, g.height - 4);
  if ($('#overlay').checked) {
    ctx.save(); ctx.beginPath(); ctx.rect(g.left, g.top, g.plotWidth, g.plotHeight); ctx.clip();
    for (const signal of state.rows) {
      const x = g.left + (signal.frequency_low_mhz - a.analysis_low_mhz) / a.analysis_bandwidth_mhz * g.plotWidth;
      const y = g.top + (1 - signal.time_stop_ms / a.duration_ms) * g.plotHeight;
      const width = (signal.frequency_high_mhz - signal.frequency_low_mhz) / a.analysis_bandwidth_mhz * g.plotWidth;
      const height = (signal.time_stop_ms - signal.time_start_ms) / a.duration_ms * g.plotHeight;
      ctx.strokeStyle = colors[signal.status]; ctx.lineWidth = signal.id === state.selected ? 2 : 1;
      if (height < 1) { ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + width, y); ctx.stroke(); }
      else ctx.strokeRect(x, y, width, height);
    }
    ctx.restore();
  }
}

$('#spectrogram').onpointermove = event => {
  if (!state.current) return;
  const g = geometry(), bounds = event.currentTarget.getBoundingClientRect();
  const x = (event.clientX - bounds.left - g.left) / g.plotWidth, y = (event.clientY - bounds.top - g.top) / g.plotHeight;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  const a = state.current.acquisition, spec = state.current.spectrogram;
  const frequency = a.analysis_low_mhz + x * a.analysis_bandwidth_mhz;
  const row = Math.min(spec.rows - 1, Math.floor((1 - y) * spec.rows));
  const column = Math.max(0, Math.min(spec.columns - 1, Math.floor((frequency - spec.low_mhz) / (spec.high_mhz - spec.low_mhz) * spec.columns)));
  $('#chart-readout').textContent = `${frequency.toFixed(2)} MHz · 文件采样时间 ${(a.duration_ms * (1 - y)).toFixed(3)} ms · 预览格 ${spec.db[row][column].toFixed(2)} dB（相对值，非校准 dBm）`;
};
$('#overlay').onchange = draw;
$('#history-filter').onchange = renderHistory;
document.querySelectorAll('[data-filter]').forEach(button => button.onclick = () => { state.filter = button.dataset.filter; renderEvidence(); });
window.addEventListener('resize', draw);
const showError = error => { $('#load-error').hidden = false; $('#load-error').textContent = error.message + '。请刷新页面，或从 GitHub 查看实采案例说明。'; $('#case-detail').setAttribute('aria-busy', 'false'); };
window.addEventListener('hashchange', () => loadCase().catch(showError));
readJSON('data/index.json?v=agile-full').then(async index => { state.index = index; renderIndex(); await loadCase(); }).catch(showError);
