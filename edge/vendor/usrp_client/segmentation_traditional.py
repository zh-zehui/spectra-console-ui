# -*- coding: utf-8 -*-
"""Traditional STFT segmentation plugin (energy-map edition, Client_New).

Pipeline:
  matrix noise floor -> hot frequency bins / row-burst mask -> energy map
  -> opening + 4-connected components -> quality filters -> box fusion -> Top-K
"""
from __future__ import annotations

from typing import Any

import numpy as np

from segmentation_contracts import SegmentationRequest
from segmentation_registry import register_algorithm


class TraditionalSegmentationPlugin:
    name = "traditional"

    def __init__(self, params: dict[str, Any]):
        self.noise_percentile = float(params.get("noise_percentile", 20.0))
        self.global_noise_percentile = float(
            params.get("global_noise_percentile", 20.0)
        )
        self.global_margin_db = float(params.get("global_margin_db", 12.0))
        self.persistent_threshold_db = float(
            params.get("persistent_threshold_db", 12.0)
        )
        self.frequency_smoothing_bins = max(
            1, int(params.get("frequency_smoothing_bins", 5))
        )
        self.cfar_threshold_db = float(params.get("cfar_threshold_db", 10.0))
        self.cfar_training_bins = max(1, int(params.get("cfar_training_bins", 12)))
        self.cfar_guard_bins = max(0, int(params.get("cfar_guard_bins", 2)))
        self.min_event_frames = max(1, int(params.get("min_event_frames", 4)))
        self.min_event_bins = max(1, int(params.get("min_event_bins", 3)))
        self.morphology_iterations = max(0, int(params.get("morphology_iterations", 1)))
        self.persistence_window_frames = max(
            1, int(params.get("persistence_window_frames", 5))
        )
        self.persistence_min_hits = max(
            1,
            min(
                self.persistence_window_frames,
                int(params.get("persistence_min_hits", 3)),
            ),
        )
        self.persistent_min_fraction = float(
            params.get("persistent_min_fraction", 0.5)
        )
        self.min_snr_db = float(params.get("min_snr_db", 8.0))
        self.max_box_freq_fraction = float(params.get("max_box_freq_fraction", 0.85))
        self.min_box_occupancy = float(params.get("min_box_occupancy", 0.25))
        self.fuse_freq_iou = float(params.get("fuse_freq_iou", 0.2))
        self.fuse_freq_gap_bins = max(0, int(params.get("fuse_freq_gap_bins", 2)))
        self.fuse_time_gap_frames = max(0, int(params.get("fuse_time_gap_frames", 4)))
        self.max_events_per_window = max(1, int(params.get("max_events_per_window", 20)))

        if not 0.0 <= self.noise_percentile <= 100.0:
            raise ValueError("traditional noise_percentile must be in [0, 100]")
        if not 0.0 <= self.global_noise_percentile <= 100.0:
            raise ValueError(
                "traditional global_noise_percentile must be in [0, 100]"
            )
        if self.global_margin_db < 0.0:
            raise ValueError("traditional global_margin_db must be non-negative")
        if not 0.0 < self.persistent_min_fraction <= 1.0:
            raise ValueError(
                "traditional persistent_min_fraction must be in (0, 1]"
            )
        if self.cfar_threshold_db < 0.0:
            raise ValueError("traditional cfar_threshold_db must be non-negative")
        if self.persistent_threshold_db < 0.0:
            raise ValueError("traditional persistent_threshold_db must be non-negative")
        if not 0.0 < self.max_box_freq_fraction <= 1.0:
            raise ValueError("traditional max_box_freq_fraction must be in (0, 1]")
        if not 0.0 <= self.min_box_occupancy <= 1.0:
            raise ValueError("traditional min_box_occupancy must be in [0, 1]")
        if not 0.0 <= self.fuse_freq_iou <= 1.0:
            raise ValueError("traditional fuse_freq_iou must be in [0, 1]")

    @staticmethod
    def _connected_components(mask: np.ndarray) -> list[np.ndarray]:
        height, width = mask.shape
        visited = np.zeros_like(mask, dtype=bool)
        components: list[np.ndarray] = []
        neighbors = ((-1, 0), (1, 0), (0, -1), (0, 1))
        for row, col in zip(*np.nonzero(mask)):
            if visited[row, col]:
                continue
            stack = [(int(row), int(col))]
            visited[row, col] = True
            points: list[tuple[int, int]] = []
            while stack:
                r, c = stack.pop()
                points.append((r, c))
                for dr, dc in neighbors:
                    nr, nc = r + dr, c + dc
                    if (
                        0 <= nr < height
                        and 0 <= nc < width
                        and mask[nr, nc]
                        and not visited[nr, nc]
                    ):
                        visited[nr, nc] = True
                        stack.append((nr, nc))
            components.append(np.asarray(points, dtype=np.int32))
        return components

    @staticmethod
    def _dilate(mask: np.ndarray) -> np.ndarray:
        padded = np.pad(mask, ((1, 1), (1, 1)), constant_values=False)
        result = np.zeros_like(mask)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                result |= padded[
                    1 + dr : 1 + dr + mask.shape[0],
                    1 + dc : 1 + dc + mask.shape[1],
                ]
        return result

    @staticmethod
    def _erode(mask: np.ndarray) -> np.ndarray:
        padded = np.pad(mask, ((1, 1), (1, 1)), constant_values=True)
        result = np.ones_like(mask)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                result &= padded[
                    1 + dr : 1 + dr + mask.shape[0],
                    1 + dc : 1 + dc + mask.shape[1],
                ]
        return result

    def _cleanup(self, mask: np.ndarray) -> np.ndarray:
        cleaned = mask
        for _ in range(self.morphology_iterations):
            cleaned = self._dilate(self._erode(cleaned))
        return cleaned

    @staticmethod
    def _median_smooth_1d(values: np.ndarray, width: int) -> np.ndarray:
        vector = np.asarray(values, dtype=np.float32).reshape(-1)
        width = max(1, int(width))
        if width <= 1 or vector.size <= 2:
            return vector.copy()
        if width % 2 == 0:
            width += 1
        radius = width // 2
        padded = np.pad(vector, (radius, radius), mode="edge")
        windows = np.stack(
            [padded[offset : offset + vector.size] for offset in range(width)],
            axis=0,
        )
        return np.median(windows, axis=0).astype(np.float32, copy=False)

    @staticmethod
    def _temporal_persistence(
        mask: np.ndarray, window: int, min_hits: int
    ) -> np.ndarray:
        height = int(mask.shape[0])
        window = max(1, min(int(window), height))
        min_hits = max(1, min(int(min_hits), window))
        if window == 1:
            return mask.copy()
        left = (window - 1) // 2
        right = window - 1 - left
        padded = np.pad(mask.astype(np.int16), ((left, right), (0, 0)))
        cumulative = np.concatenate(
            [np.zeros((1, mask.shape[1]), dtype=np.int32), np.cumsum(padded, axis=0)],
            axis=0,
        )
        counts = cumulative[window:] - cumulative[:-window]
        return mask & (counts >= min_hits)

    @staticmethod
    def _freq_iou(a: dict, b: dict) -> float:
        a1, a2 = int(a["x1"]), int(a["x2"])
        b1, b2 = int(b["x1"]), int(b["x2"])
        inter = max(0, min(a2, b2) - max(a1, b1) + 1)
        if inter <= 0:
            return 0.0
        union = (a2 - a1 + 1) + (b2 - b1 + 1) - inter
        return float(inter / max(1, union))

    @staticmethod
    def _freq_gap(a: dict, b: dict) -> int:
        a1, a2 = int(a["x1"]), int(a["x2"])
        b1, b2 = int(b["x1"]), int(b["x2"])
        if a2 < b1:
            return b1 - a2 - 1
        if b2 < a1:
            return a1 - b2 - 1
        return 0

    @staticmethod
    def _time_gap(a: dict, b: dict) -> int:
        a1, a2 = int(a["y1"]), int(a["y2"])
        b1, b2 = int(b["y1"]), int(b["y2"])
        if a2 < b1:
            return b1 - a2 - 1
        if b2 < a1:
            return a1 - b2 - 1
        return 0

    def _should_fuse(self, a: dict, b: dict) -> bool:
        freq_close = (
            self._freq_iou(a, b) >= self.fuse_freq_iou
            or self._freq_gap(a, b) <= self.fuse_freq_gap_bins
        )
        time_close = self._time_gap(a, b) <= self.fuse_time_gap_frames
        return freq_close and time_close

    def _event_from_points(
        self,
        *,
        points: np.ndarray,
        values: np.ndarray,
        persistent_mask: np.ndarray,
        matrix_floor: float,
        request: SegmentationRequest,
        index: int,
    ) -> dict | None:
        y1, x1 = points.min(axis=0)
        y2, x2 = points.max(axis=0)
        height = int(y2 - y1 + 1)
        width = int(x2 - x1 + 1)
        n_freq = int(values.shape[1])
        max_width = max(self.min_event_bins, int(self.max_box_freq_fraction * n_freq))
        if height < self.min_event_frames or width < self.min_event_bins:
            return None
        if width > max_width:
            return None

        samples = values[points[:, 0], points[:, 1]]
        peak_db = float(np.max(samples))
        mean_db = float(np.mean(samples))
        noise_db = float(matrix_floor)
        snr_db = peak_db - noise_db
        if snr_db < self.min_snr_db:
            return None

        occupancy = float(len(points) / (height * width))
        if occupancy < self.min_box_occupancy and width >= 2 * self.min_event_bins:
            return None

        frame_ids = getattr(request, "frame_ids", None)
        if frame_ids is not None and len(frame_ids):
            ids = np.asarray(frame_ids).reshape(-1)
            event_start_frame_id = int(ids[min(len(ids) - 1, int(y1))])
            event_end_frame_id = int(ids[min(len(ids) - 1, int(y2))])
        else:
            event_start_frame_id = int(request.start_frame_id + y1)
            event_end_frame_id = int(request.start_frame_id + y2)

        persist_frac = float(np.mean(persistent_mask[points[:, 0], points[:, 1]]))
        energy_score = max(0.0, mean_db - matrix_floor) * occupancy * width * height
        return {
            "id": f"ev_traditional_{request.start_frame_id}_{index}",
            "label": (
                "persistent_signal"
                if persist_frac >= self.persistent_min_fraction
                else "signal"
            ),
            "method": self.name,
            "score": round(float(np.clip(snr_db / 30.0, 0.0, 0.99)), 4),
            "box": {"x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2)},
            "window_start_frame_id": int(request.start_frame_id),
            "window_end_frame_id": event_end_frame_id,
            "event_start_frame_id": event_start_frame_id,
            "event_end_frame_id": event_end_frame_id,
            "center_freq_bin": round(float(np.mean(points[:, 1])), 3),
            "freq_width_bins": width,
            "time_height_frames": height,
            "peak_db": round(peak_db, 2),
            "mean_db": round(mean_db, 2),
            "noise_db": round(noise_db, 2),
            "snr_db": round(snr_db, 2),
            "peak_to_average_db": round(peak_db - mean_db, 2),
            "occupancy": round(occupancy, 4),
            "persistence_fraction": round(persist_frac, 4),
            "energy_score": float(energy_score),
            "_points": points,
        }

    def _fuse_events(
        self,
        events: list[dict],
        values: np.ndarray,
        persistent_mask: np.ndarray,
        matrix_floor: float,
        request: SegmentationRequest,
    ) -> list[dict]:
        if not events:
            return []
        remaining = sorted(events, key=lambda e: (e["box"]["x1"], e["box"]["y1"]))
        fused: list[dict] = []
        while remaining:
            current = remaining.pop(0)
            changed = True
            while changed:
                changed = False
                keep: list[dict] = []
                for other in remaining:
                    if self._should_fuse(current["box"], other["box"]):
                        points = np.concatenate(
                            (current["_points"], other["_points"]), axis=0
                        )
                        merged = self._event_from_points(
                            points=points,
                            values=values,
                            persistent_mask=persistent_mask,
                            matrix_floor=matrix_floor,
                            request=request,
                            index=len(fused),
                        )
                        if merged is not None:
                            current = merged
                            changed = True
                        else:
                            keep.append(other)
                    else:
                        keep.append(other)
                remaining = keep
            fused.append(current)
        return fused

    def detect(self, request: SegmentationRequest) -> list[dict]:
        values = np.asarray(request.window_db, dtype=np.float32)
        if values.ndim != 2 or not values.size:
            return []

        finite_values = np.where(np.isfinite(values), values, np.nan)
        matrix_floor = float(np.nanpercentile(finite_values, self.noise_percentile))

        col_level = np.nanmedian(finite_values, axis=0).astype(np.float32)
        col_level = self._median_smooth_1d(col_level, self.frequency_smoothing_bins)
        hot_bins = col_level >= (matrix_floor + self.persistent_threshold_db)
        persistent_mask = hot_bins[None, :] & (
            values >= (matrix_floor + 0.7 * self.persistent_threshold_db)
        )

        row_med = np.nanmedian(finite_values, axis=1).astype(np.float32)
        burst_mask = (
            np.isfinite(values)
            & ((values - row_med[:, None]) >= self.cfar_threshold_db)
            & (values >= (matrix_floor + self.cfar_threshold_db))
        )
        burst_mask = self._temporal_persistence(
            burst_mask,
            self.persistence_window_frames,
            self.persistence_min_hits,
        )

        mask = self._cleanup(persistent_mask | burst_mask)

        candidates: list[dict] = []
        for index, points in enumerate(self._connected_components(mask)):
            event = self._event_from_points(
                points=points,
                values=values,
                persistent_mask=persistent_mask,
                matrix_floor=matrix_floor,
                request=request,
                index=index,
            )
            if event is not None:
                candidates.append(event)

        fused = self._fuse_events(
            candidates, values, persistent_mask, matrix_floor, request
        )
        fused.sort(key=lambda e: float(e.get("energy_score", 0.0)), reverse=True)
        selected = fused[: self.max_events_per_window]

        events: list[dict] = []
        for index, event in enumerate(selected):
            event = dict(event)
            event.pop("_points", None)
            event.pop("energy_score", None)
            event["id"] = f"ev_traditional_{request.start_frame_id}_{index}"
            events.append(event)
        return events

    def close(self) -> None:
        return None


register_algorithm("traditional", TraditionalSegmentationPlugin)
