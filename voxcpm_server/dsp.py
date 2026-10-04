"""Small numpy DSP helpers: tempo/pitch change without artefacts, fades, joins."""

from __future__ import annotations

from typing import Iterable

import numpy as np


def wsola_time_stretch(x: np.ndarray, rate: float, sr: int) -> np.ndarray:
    """Change tempo by `rate` (>1 = faster) while keeping pitch (WSOLA overlap-add)."""
    x = np.asarray(x, dtype=np.float32)
    rate = float(rate)
    if abs(rate - 1.0) < 0.01 or len(x) < int(sr * 0.1):
        return x
    frame = max(256, int(sr * 0.040))      # 40 ms grains
    hop_out = frame // 2
    hop_in = hop_out * rate
    tol = int(sr * 0.012)                  # +/-12 ms alignment search
    window = np.hanning(frame).astype(np.float32)
    target_len = int(len(x) / rate)
    out = np.zeros(target_len + frame, dtype=np.float32)
    norm = np.zeros(target_len + frame, dtype=np.float32)
    padded = np.concatenate([np.zeros(tol, np.float32), x, np.zeros(frame * 2 + tol, np.float32)])
    template = None
    pos_out, k = 0, 0
    while pos_out < target_len:
        nominal = int(round(k * hop_in)) + tol
        if nominal + frame + tol > len(padded):
            break
        if template is None:
            best = nominal
        else:
            seg = padded[nominal - tol: nominal + tol + frame]
            corr = np.correlate(seg, template, mode="valid")
            best = nominal - tol + int(np.argmax(corr))
        grain = padded[best: best + frame]
        out[pos_out: pos_out + frame] += grain * window
        norm[pos_out: pos_out + frame] += window
        template = padded[best + hop_out: best + hop_out + frame]
        pos_out += hop_out
        k += 1
    norm[norm < 1e-3] = 1.0
    return (out / norm)[:target_len]


def apply_speed_and_pitch(x: np.ndarray, sr: int, speed: float = 1.0, semitones: float = 0.0) -> np.ndarray:
    """Tempo change without pitch change, and pitch shift without tempo change."""
    x = np.asarray(x, dtype=np.float32)
    speed = float(speed or 1.0)
    semitones = float(semitones or 0.0)
    if abs(semitones) >= 0.05:
        factor = 2.0 ** (semitones / 12.0)
        stretched = wsola_time_stretch(x, 1.0 / factor, sr)
        n_out = max(1, int(round(len(stretched) / factor)))
        x = np.interp(np.linspace(0, len(stretched) - 1, n_out), np.arange(len(stretched)), stretched).astype(np.float32)
    if abs(speed - 1.0) >= 0.02:
        x = wsola_time_stretch(x, speed, sr)
    return x


def fade_edges(x: np.ndarray, sr: int, ms: float = 6.0) -> np.ndarray:
    """Short linear fades so joined pieces never click."""
    out = np.array(x, dtype=np.float32, copy=True)
    n = min(len(out) // 2, int(round(sr * ms / 1000.0)))
    if n > 0:
        ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
        out[:n] *= ramp
        out[-n:] *= ramp[::-1]
    return out


def silence(sr: int, ms: float) -> np.ndarray:
    return np.zeros(max(0, int(round(sr * ms / 1000.0))), dtype=np.float32)


def concat(parts: Iterable[np.ndarray]) -> np.ndarray:
    parts = [np.asarray(p, dtype=np.float32).reshape(-1) for p in parts]
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def limit_peak(x: np.ndarray, ceiling: float = 0.98) -> np.ndarray:
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak > 1.0:
        return (x / peak * ceiling).astype(np.float32)
    return x.astype(np.float32, copy=False)
