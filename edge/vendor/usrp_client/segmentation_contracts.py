# -*- coding: utf-8 -*-
"""Common contracts shared by segmentation plugins and the receiver."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np


@dataclass(frozen=True)
class SegmentationRequest:
    """One complete STFT window passed from the receiver to an algorithm."""

    window_db: np.ndarray
    start_frame_id: int
    stream_id: str
    fft_size: int
    window_size: int
    # Publisher-owned frame numbers for every row in ``window_db``.  A SUB
    # subscriber may miss frames, so row ``y`` is not necessarily
    # ``start_frame_id + y``.
    frame_ids: np.ndarray | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SegmentationPlugin(Protocol):
    """Protocol implemented by every pluggable segmentation algorithm."""

    name: str

    def detect(self, request: SegmentationRequest) -> list[dict]:
        """Return structured events for one STFT window."""

    def close(self) -> None:
        """Release model/process/resources owned by the plugin."""
