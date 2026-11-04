#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wire formats for 1D PSD frames and 2D STFT matrices.

This ``Client_New`` copy adds ``stft2d.v1`` without changing the original
``Client/stft_protocol.py``.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass

import numpy as np


STFT_MAGIC = b"STF1"
STFT_VERSION = 1
_HEADER = struct.Struct("<4sBBHQII")
STFT_HEADER_SIZE = _HEADER.size

STFT2D_MAGIC = "stft2d.v1"
TWIN_IQ_MAGIC = "twin.v1"
SEG_TOPIC = "seg.v1"


@dataclass(frozen=True)
class DecodedStftFrame:
    """One decoded 1D STFT/PSD frame and its publisher-owned source frame number."""

    source_frame_id: int | None
    values: np.ndarray


@dataclass(frozen=True)
class DecodedStft2dMatrix:
    """One decoded 2D STFT matrix already expressed in dB."""

    chunk_id: int
    values_db: np.ndarray  # shape (n_time, n_freq), float32
    header: dict


@dataclass(frozen=True)
class DecodedTwinIq:
    """One decoded Twin IQ window (complex64 samples)."""

    chunk_id: int
    iq: np.ndarray  # shape (n_iq,), complex64
    header: dict


def encode_stft_frame(source_frame_id: int, values) -> bytes:
    """Encode one PSD vector with a source-generated frame number."""
    vector = np.asarray(values, dtype="<f4").reshape(-1)
    payload = vector.tobytes(order="C")
    header = _HEADER.pack(
        STFT_MAGIC,
        STFT_VERSION,
        0,
        STFT_HEADER_SIZE,
        int(source_frame_id),
        int(vector.size),
        len(payload),
    )
    return header + payload


def decode_stft_frames(raw: bytes, expected_bins: int | None = None) -> list[DecodedStftFrame] | None:
    """Decode one or more 1D STFT records carried in one ZMQ message."""
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        return None
    raw = bytes(raw)

    if len(raw) >= STFT_HEADER_SIZE and raw[:4] == STFT_MAGIC:
        decoded: list[DecodedStftFrame] = []
        offset = 0
        while offset < len(raw):
            if len(raw) - offset < STFT_HEADER_SIZE or raw[offset : offset + 4] != STFT_MAGIC:
                return None
            try:
                magic, version, _flags, header_size, source_frame_id, bin_count, payload_bytes = _HEADER.unpack_from(
                    raw, offset
                )
            except struct.error:
                return None
            if magic != STFT_MAGIC or version != STFT_VERSION:
                return None
            if header_size < STFT_HEADER_SIZE or header_size > len(raw) - offset:
                return None
            if bin_count <= 0 or payload_bytes != bin_count * np.dtype("<f4").itemsize:
                return None
            record_bytes = header_size + payload_bytes
            if record_bytes > len(raw) - offset:
                return None
            if expected_bins is not None and int(expected_bins) != int(bin_count):
                return None
            values = np.frombuffer(
                raw,
                dtype="<f4",
                count=bin_count,
                offset=offset + header_size,
            )
            decoded.append(DecodedStftFrame(int(source_frame_id), values))
            offset += record_bytes
        return decoded

    if expected_bins is None or int(expected_bins) <= 0:
        return None
    frame_bytes = int(expected_bins) * np.dtype("<f4").itemsize
    if len(raw) % frame_bytes:
        return None
    return [
        DecodedStftFrame(
            None,
            np.frombuffer(raw, dtype="<f4", count=int(expected_bins), offset=offset),
        )
        for offset in range(0, len(raw), frame_bytes)
    ]


def decode_stft_frame(raw: bytes, expected_bins: int | None = None) -> DecodedStftFrame | None:
    """Decode exactly one 1D STFT record; return ``None`` for an aggregate."""
    frames = decode_stft_frames(raw, expected_bins)
    if frames is None or len(frames) != 1:
        return None
    return frames[0]


def freq_axis_max_pool(psd, out_bins: int) -> np.ndarray:
    """Split a 1D spectrum into ``out_bins`` segments and keep the max of each."""
    x = np.asarray(psd, dtype=np.float64).reshape(-1)
    n = int(x.size)
    m = int(out_bins)
    if n <= 0:
        return np.zeros(max(1, m), dtype=np.float32)
    if m <= 0 or m == n:
        return x.astype(np.float32, copy=False)
    if m > n:
        m = n
    edges = np.linspace(0, n, m + 1, dtype=np.int64)
    out = np.empty(m, dtype=np.float32)
    for j in range(m):
        a = int(edges[j])
        b = int(edges[j + 1])
        if b <= a:
            b = min(a + 1, n)
        out[j] = float(np.max(x[a:b]))
    return out


def iq_to_stft_db(
    iq,
    *,
    fft_size: int,
    hop: int,
    resize_bins: int | None = None,
    window: np.ndarray | None = None,
) -> np.ndarray:
    """Compute a time-major dB STFT matrix from one IQ vector.

    Returns ``float32`` array with shape ``(n_time, n_freq)``.
    """
    samples = np.asarray(iq, dtype=np.complex64).reshape(-1)
    fft_size = int(fft_size)
    hop = max(1, int(hop))
    if samples.size < fft_size:
        n_freq = int(resize_bins) if resize_bins else fft_size
        return np.zeros((0, max(1, n_freq)), dtype=np.float32)

    if window is None:
        # Periodic-ish raised cosine substitute when scipy/GR window unavailable.
        n = np.arange(fft_size, dtype=np.float64)
        window = 0.35875 - 0.48829 * np.cos(2 * np.pi * n / fft_size)
        window += 0.14128 * np.cos(4 * np.pi * n / fft_size)
        window -= 0.01168 * np.cos(6 * np.pi * n / fft_size)
    win = np.asarray(window, dtype=np.float64).reshape(-1)
    if win.size != fft_size:
        raise ValueError("window length must equal fft_size")

    n_frames = 1 + (samples.size - fft_size) // hop
    shape = (n_frames, fft_size)
    strides = (samples.strides[0] * hop, samples.strides[0])
    framed = np.lib.stride_tricks.as_strided(samples, shape=shape, strides=strides)
    framed = np.array(framed, dtype=np.complex128, copy=True) * win[None, :]
    spectrum = np.fft.fftshift(np.fft.fft(framed, n=fft_size, axis=1), axes=1)
    power = (spectrum.real * spectrum.real + spectrum.imag * spectrum.imag).astype(
        np.float32, copy=False
    )

    if resize_bins is not None:
        out_bins = max(1, int(resize_bins))
        if out_bins != fft_size:
            power = np.stack(
                [freq_axis_max_pool(row, out_bins) for row in power],
                axis=0,
            ).astype(np.float32, copy=False)

    return (10.0 * np.log10(np.maximum(power, 1e-20))).astype(np.float32, copy=False)


def encode_stft2d_parts(
    *,
    matrix_db,
    fs_hz: float,
    center_freq_hz: float,
    n_iq: int,
    chunk_id: int,
    fft_size: int,
    hop: int,
    freq_resize: bool,
    t_unix: float | None = None,
) -> tuple[bytes, bytes]:
    """Return ``(json_header_bytes, float32_payload_bytes)`` for stft2d.v1."""
    import time as _time

    values = np.asarray(matrix_db, dtype="<f4")
    if values.ndim != 2:
        raise ValueError("stft2d matrix must be 2-D")
    n_time, n_freq = int(values.shape[0]), int(values.shape[1])
    header = {
        "magic": STFT2D_MAGIC,
        "fs_hz": float(fs_hz),
        "center_freq_hz": float(center_freq_hz),
        "n_iq": int(n_iq),
        "chunk_id": int(chunk_id),
        "t_unix": float(_time.time() if t_unix is None else t_unix),
        "fft_size": int(fft_size),
        "hop": int(hop),
        "n_time": n_time,
        "n_freq": n_freq,
        "freq_resize": bool(freq_resize),
        "unit": "dB",
        "layout": "time_major",
    }
    payload = np.ascontiguousarray(values, dtype="<f4").tobytes(order="C")
    return json.dumps(header, separators=(",", ":")).encode("utf-8"), payload


def decode_stft2d_parts(header_raw: bytes, payload_raw: bytes) -> DecodedStft2dMatrix | None:
    """Decode one stft2d.v1 multipart message."""
    try:
        header = json.loads(header_raw.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(header, dict) or header.get("magic") != STFT2D_MAGIC:
        return None
    try:
        n_time = int(header["n_time"])
        n_freq = int(header["n_freq"])
        chunk_id = int(header.get("chunk_id", 0))
    except Exception:
        return None
    expected = n_time * n_freq * np.dtype("<f4").itemsize
    if n_time < 0 or n_freq <= 0 or len(payload_raw) != expected:
        return None
    values = np.frombuffer(payload_raw, dtype="<f4", count=n_time * n_freq).reshape(
        n_time, n_freq
    )
    return DecodedStft2dMatrix(chunk_id=chunk_id, values_db=values, header=header)


def encode_twin_iq_parts(
    *,
    iq,
    fs_hz: float,
    center_freq_hz: float,
    chunk_id: int,
    t_unix: float | None = None,
) -> tuple[bytes, bytes]:
    """Return ``(json_header_bytes, complex64_payload_bytes)`` for twin.v1."""
    import time as _time

    samples = np.ascontiguousarray(np.asarray(iq, dtype="<c8").reshape(-1))
    header = {
        "magic": TWIN_IQ_MAGIC,
        "fs_hz": float(fs_hz),
        "center_freq_hz": float(center_freq_hz),
        "n_iq": int(samples.size),
        "chunk_id": int(chunk_id),
        "t_unix": float(_time.time() if t_unix is None else t_unix),
    }
    return json.dumps(header, separators=(",", ":")).encode("utf-8"), samples.tobytes(
        order="C"
    )


def decode_twin_iq_parts(header_raw: bytes, payload_raw: bytes) -> DecodedTwinIq | None:
    """Decode one twin.v1 multipart message."""
    try:
        header = json.loads(header_raw.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(header, dict) or header.get("magic") != TWIN_IQ_MAGIC:
        return None
    try:
        n_iq = int(header["n_iq"])
        chunk_id = int(header.get("chunk_id", 0))
    except Exception:
        return None
    expected = n_iq * np.dtype("<c8").itemsize
    if n_iq <= 0 or len(payload_raw) != expected:
        return None
    iq = np.frombuffer(payload_raw, dtype="<c8", count=n_iq)
    return DecodedTwinIq(chunk_id=chunk_id, iq=iq, header=header)


def encode_seg_v1_parts(
    payload: dict,
    *,
    stft_header: bytes | None = None,
    stft_payload: bytes | None = None,
) -> list[bytes]:
    """Pack a ``seg.v1`` PUB message.

    Layout:
    * STFT subscribe path: ``[topic, json]`` — events only
    * IQ subscribe path: ``[topic, stft2d_header, stft2d_payload, json]``

    JSON is always the last frame so existing ``frames[-1]`` JSON consumers
    keep working.
    """
    topic = SEG_TOPIC.encode("utf-8")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if stft_header is not None and stft_payload is not None:
        return [topic, stft_header, stft_payload, body]
    return [topic, body]


def decode_seg_v1_parts(
    parts,
    expected_topic: str = SEG_TOPIC,
) -> tuple[dict | None, DecodedStft2dMatrix | None]:
    """Parse a ``seg.v1`` multipart; optional STFT is in frames 1-2."""
    if not parts or len(parts) < 2:
        return None, None
    message_topic = parts[0].decode("utf-8", errors="replace") if parts[0] else ""
    if expected_topic and message_topic and message_topic != expected_topic:
        return None, None
    try:
        payload = json.loads(parts[-1].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    matrix = None
    if len(parts) >= 4:
        matrix = decode_stft2d_parts(parts[1], parts[2])
    return payload, matrix


STFT_UPLOAD_DTYPES = ("float32", "float16", "int8")


def normalize_stft_upload_dtype(value, default: str = "float32") -> str:
    raw = str(value or default).strip().lower()
    aliases = {
        "f32": "float32",
        "fp32": "float32",
        "none": "float32",
        "off": "float32",
        "f16": "float16",
        "fp16": "float16",
        "half": "float16",
        "i8": "int8",
        "uint8": "int8",
        "u8": "int8",
    }
    raw = aliases.get(raw, raw)
    return raw if raw in STFT_UPLOAD_DTYPES else default


def encode_stft_upload_psd(
    row_db,
    dtype: str = "float32",
    *,
    db_min: float = -120.0,
    db_max: float = -20.0,
) -> tuple[dict, bytes]:
    """Encode one PSD row for Web uplink: JSON fields + binary payload.

    int8 uses a fixed absolute dB range (db_min..db_max) for every row —
    not per-row min/max — so waterfall levels stay comparable over time.
    """
    vec = np.asarray(row_db, dtype=np.float32).reshape(-1)
    dtype = normalize_stft_upload_dtype(dtype)
    meta = {
        "payload_encoding": "binary.v1",
        "psd_dtype": dtype,
        "psd_n_bins": int(vec.size),
        "fft_size": int(vec.size),
    }
    if dtype == "float32":
        return meta, np.asarray(vec, dtype="<f4").tobytes(order="C")
    if dtype == "float16":
        return meta, np.asarray(vec, dtype="<f2").tobytes(order="C")
    vmin = float(db_min)
    vmax = float(db_max)
    if not np.isfinite(vmin):
        vmin = -120.0
    if not np.isfinite(vmax) or vmax <= vmin:
        vmax = vmin + 100.0
    scale = (vmax - vmin) / 255.0
    q = np.clip(np.rint((vec - vmin) / scale), 0, 255).astype(np.uint8)
    meta["psd_scale_min"] = vmin
    meta["psd_scale_max"] = vmax
    return meta, q.tobytes(order="C")


def decode_stft_upload_psd(meta: dict | None, payload: bytes | bytearray | memoryview | None):
    """Decode binary PSD uplink back to float32."""
    if not meta or payload is None:
        return None
    dtype = normalize_stft_upload_dtype(meta.get("psd_dtype") or meta.get("stft_upload_dtype"))
    n = int(meta.get("psd_n_bins") or meta.get("fft_size") or 0)
    raw = bytes(payload)
    if dtype == "float32":
        if n <= 0:
            n = len(raw) // 4
        if n <= 0 or len(raw) < n * 4:
            return None
        return np.frombuffer(raw, dtype="<f4", count=n).astype(np.float32, copy=True)
    if dtype == "float16":
        if n <= 0:
            n = len(raw) // 2
        if n <= 0 or len(raw) < n * 2:
            return None
        return np.frombuffer(raw, dtype="<f2", count=n).astype(np.float32, copy=True)
    if n <= 0:
        n = len(raw)
    if n <= 0 or len(raw) < n:
        return None
    q = np.frombuffer(raw, dtype=np.uint8, count=n)
    vmin = float(meta.get("psd_scale_min", -120.0))
    vmax = float(meta.get("psd_scale_max", -20.0))
    if not np.isfinite(vmin):
        vmin = -120.0
    if not np.isfinite(vmax) or vmax <= vmin:
        vmax = vmin + 1.0
    scale = (vmax - vmin) / 255.0
    return (q.astype(np.float32) * scale + vmin).astype(np.float32, copy=False)
