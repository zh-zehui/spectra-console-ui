// Match the device workbench: latest time at the top and maximum pooling in dB.
export function palette(value) {
  const stops = [[9,24,19],[17,48,37],[34,88,61],[79,137,78],[160,180,101],[228,210,135]];
  const position = Math.max(0, Math.min(.999, value)) * (stops.length - 1);
  const index = Math.floor(position), weight = position - index;
  return stops[index].map((channel, i) => Math.round(channel + (stops[index + 1][i] - channel) * weight));
}

export function maximumDb(spec, frequencyStart, frequencyStop, timeStart, timeStop, sourceStart, sourceStop) {
  const x0 = Math.max(0, Math.floor((frequencyStart - spec.low_mhz) / (spec.high_mhz - spec.low_mhz) * spec.columns));
  const x1 = Math.min(spec.columns - 1, Math.ceil((frequencyStop - spec.low_mhz) / (spec.high_mhz - spec.low_mhz) * spec.columns) - 1);
  const y0 = Math.max(0, Math.floor((timeStart - sourceStart) / (sourceStop - sourceStart) * spec.rows));
  const y1 = Math.min(spec.rows - 1, Math.ceil((timeStop - sourceStart) / (sourceStop - sourceStart) * spec.rows) - 1);
  let peak = -Infinity;
  for (let y = y0; y <= Math.max(y0, y1) && y < spec.rows; y++) {
    for (let x = x0; x <= Math.max(x0, x1) && x < spec.columns; x++) peak = Math.max(peak, spec.db[y][x]);
  }
  return peak;
}

export function pixelBounds(view, x, y, width, height) {
  return {
    frequencyStart: view.low_mhz + x / width * (view.high_mhz - view.low_mhz),
    frequencyStop: view.low_mhz + (x + 1) / width * (view.high_mhz - view.low_mhz),
    timeStart: view.end_ms - (y + 1) / height * (view.end_ms - view.start_ms),
    timeStop: view.end_ms - y / height * (view.end_ms - view.start_ms)
  };
}

export function createRaster(spec, view, width, height) {
  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const bounds = pixelBounds(view, x, y, width, height);
    const power = maximumDb(spec, bounds.frequencyStart, bounds.frequencyStop, bounds.timeStart, bounds.timeStop, view.start_ms, view.end_ms);
    const color = palette(Number.isFinite(power) ? (power - spec.min_db) / (spec.max_db - spec.min_db) : 0);
    const position = (y * width + x) * 4;
    pixels.set([...color, 255], position);
  }
  return pixels;
}

export function visibleSignals(signals, view, enabled) {
  return signals.filter(signal => (!enabled || enabled.has(signal.status)) && signal.time_start_ms < view.end_ms && signal.time_stop_ms > view.start_ms && signal.frequency_high_mhz > view.low_mhz && signal.frequency_low_mhz < view.high_mhz);
}
