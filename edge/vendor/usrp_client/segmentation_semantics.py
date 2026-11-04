# -*- coding: utf-8 -*-
"""Compact human-readable semantics for segmentation events.

This module deliberately contains no STFT/PSD samples.  It converts the
structured event measurements into a bounded text description suitable for
logs, operators and downstream language models.
"""
from __future__ import annotations

from typing import Any, Iterable


def _num(event: dict, *names: str, default=None):
    for name in names:
        value = event.get(name)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


def event_semantic_text(event: dict, ordinal: int = 1) -> str:
    """Render one event as dense Chinese text while retaining key numbers."""
    box = event.get("box") or {}
    label = str(event.get("label") or event.get("type") or "signal")
    method = str(event.get("method") or "unknown")
    x1 = box.get("x1", "?"); x2 = box.get("x2", "?")
    y1 = box.get("y1", event.get("start_frame_id", "?"))
    y2 = box.get("y2", event.get("end_frame_id", "?"))
    center_hz = _num(event, "center_freq_hz")
    center_bin = _num(event, "center_freq_bin")
    width_hz = _num(event, "bandwidth_hz")
    width_bin = _num(event, "bandwidth_bins", "freq_width_bins")
    duration = _num(event, "duration_frames", "time_height_frames")
    peak = _num(event, "peak_db")
    mean = _num(event, "mean_db")
    noise = _num(event, "noise_db")
    snr = _num(event, "snr_db")
    p2a = _num(event, "peak_to_average_db")
    score = _num(event, "score")

    parts = [f"事件{ordinal}: {label}，方法={method}", f"时频框=[f{x1}:{x2}, t{y1}:{y2}]"]
    if center_hz is not None:
        parts.append(f"中心频率={center_hz / 1e6:.6g}MHz")
    elif center_bin is not None:
        parts.append(f"中心频点={center_bin:.3g}")
    if width_hz is not None:
        parts.append(f"带宽={width_hz / 1e6:.6g}MHz")
    elif width_bin is not None:
        parts.append(f"带宽={width_bin:.3g}bin")
    if duration is not None:
        parts.append(f"持续={duration:.3g}帧")
    if peak is not None:
        parts.append(f"峰值={peak:.3g}dB")
    if mean is not None:
        parts.append(f"均值={mean:.3g}dB")
    if noise is not None:
        parts.append(f"噪声底={noise:.3g}dB")
    if snr is not None:
        parts.append(f"SNR={snr:.3g}dB")
    if p2a is not None:
        parts.append(f"峰均差={p2a:.3g}dB")
    if score is not None:
        parts.append(f"置信度={score:.3g}")
    return "，".join(parts)


def event_overlay_caption(event: dict, ordinal: int = 1) -> str:
    """Short waterfall label: event index, center freq, bandwidth, SNR."""
    center_hz = _num(event, "center_freq_hz")
    width_hz = _num(event, "bandwidth_hz")
    snr = _num(event, "snr_db")
    parts = [f"事件{ordinal}"]
    if center_hz is not None:
        parts.append(f"中心={center_hz / 1e6:.3f}MHz")
    if width_hz is not None:
        parts.append(f"带宽={width_hz / 1e6:.3f}MHz")
    if snr is not None:
        parts.append(f"SNR={snr:.1f}dB")
    return "，".join(parts)


def summarize_events(events: Iterable[dict] | None, *, stream_id: str = "unknown",
                     window_start_frame_id=None, window_end_frame_id=None) -> dict[str, Any]:
    """Create bounded structured and textual summaries without embedding PSD."""
    valid = [e for e in (events or []) if isinstance(e, dict)]
    texts = [event_semantic_text(event, i + 1) for i, event in enumerate(valid)]
    for event, text in zip(valid, texts):
        event["semantic_text"] = text

    if not valid:
        sentence = f"流{stream_id}：当前窗口未检测到稳定信号事件"
    else:
        snrs = [_num(e, "snr_db", default=float("-inf")) for e in valid]
        best = max(range(len(valid)), key=lambda i: snrs[i])
        sentence = (
            f"流{stream_id}：窗口{window_start_frame_id}–{window_end_frame_id}"
            f"检测到{len(valid)}个信号事件；最强事件为事件{best + 1}"
            f"（SNR={snrs[best]:.3g}dB）。"
        )
    semantic_text = sentence + (("" if sentence.endswith("；") else "；") + "；".join(texts) if texts else "")
    # Keep the language-model/log payload compact even when a noisy window
    # produces many connected regions; full numeric events remain in `events`.
    if len(semantic_text) > 8192:
        semantic_text = semantic_text[:8189] + "..."
    return {
        "semantic_text": semantic_text,
        "event_texts": texts,
        "signal_count": len(valid),
        "has_signal": bool(valid),
        "strongest_event_index": (best if valid else None),
    }
