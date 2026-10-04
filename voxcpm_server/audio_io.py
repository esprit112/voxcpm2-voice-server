"""Encoding generated audio and decoding uploaded audio.

libsndfile (bundled with the `soundfile` wheels on Windows, macOS and Linux) writes WAV, FLAC, MP3 and
Ogg/Opus itself, so FFmpeg is optional. It is only used for AAC, or as a fallback when an old
libsndfile lacks MP3/Opus support.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import wave
from typing import Tuple

import numpy as np

CONTENT_TYPES = {
    "wav": "audio/wav",
    "flac": "audio/flac",
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "ogg": "audio/ogg",
    "aac": "audio/aac",
    "pcm": "audio/pcm",
}
SUPPORTED_FORMATS = tuple(CONTENT_TYPES)


def to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def to_wav_bytes(audio: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sr))
        wf.writeframes(to_pcm16(audio))
    return buf.getvalue()


def _soundfile_encode(audio: np.ndarray, sr: int, fmt: str, subtype=None) -> bytes:
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, np.asarray(audio, dtype=np.float32), int(sr), format=fmt, subtype=subtype)
    return buf.getvalue()


def _ffmpeg_encode(audio: np.ndarray, sr: int, fmt: str) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    codec = {
        "mp3": ["-c:a", "libmp3lame", "-q:a", "2", "-f", "mp3"],
        "opus": ["-c:a", "libopus", "-b:a", "64k", "-f", "ogg"],
        "aac": ["-c:a", "aac", "-b:a", "160k", "-f", "adts"],
    }[fmt]
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(int(sr)), "-ac", "1",
         "-i", "pipe:0", *codec, "pipe:1"],
        input=to_pcm16(audio), capture_output=True, timeout=300,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:300])
    return proc.stdout


def encode(audio: np.ndarray, sr: int, fmt: str) -> Tuple[bytes, str, str]:
    """Return (bytes, content_type, format_actually_used). Falls back to WAV rather than failing."""
    fmt = (fmt or "wav").lower().strip()
    if fmt == "ogg":
        fmt = "opus"
    if fmt not in CONTENT_TYPES:
        fmt = "wav"
    if fmt == "wav":
        return to_wav_bytes(audio, sr), CONTENT_TYPES["wav"], "wav"
    if fmt == "pcm":
        return to_pcm16(audio), f"audio/pcm;rate={int(sr)};channels=1", "pcm"
    attempts = []
    if fmt == "flac":
        attempts.append(lambda: _soundfile_encode(audio, sr, "FLAC", "PCM_24"))
    elif fmt == "mp3":
        attempts.append(lambda: _soundfile_encode(audio, sr, "MP3"))
        attempts.append(lambda: _ffmpeg_encode(audio, sr, "mp3"))
    elif fmt == "opus":
        attempts.append(lambda: _soundfile_encode(audio, sr, "OGG", "OPUS"))
        attempts.append(lambda: _ffmpeg_encode(audio, sr, "opus"))
    elif fmt == "aac":
        attempts.append(lambda: _ffmpeg_encode(audio, sr, "aac"))
    for attempt in attempts:
        try:
            data = attempt()
            if data:
                return data, CONTENT_TYPES[fmt], fmt
        except Exception:  # noqa: BLE001 - try the next encoder, then fall back to WAV
            continue
    return to_wav_bytes(audio, sr), CONTENT_TYPES["wav"], "wav"


def decode_upload(data: bytes) -> Tuple[np.ndarray, int]:
    """Decode an uploaded recording (WAV/FLAC/OGG/MP3...) to mono float32."""
    try:
        import soundfile as sf

        audio, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
        return audio.mean(axis=1).astype(np.float32), int(sr)
    except Exception:
        pass
    # Plain PCM WAV via the standard library (also covers environments without libsndfile)
    with wave.open(io.BytesIO(data), "rb") as wf:
        sr, ch, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if width != 2:
        raise ValueError("Unsupported WAV sample width; please upload 16-bit WAV, FLAC, OGG or MP3.")
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        pcm = pcm.reshape(-1, ch).mean(axis=1)
    return pcm, int(sr)


def prepare_reference(audio: np.ndarray, sr: int, max_seconds: float = 30.0) -> np.ndarray:
    """Trim leading/trailing silence, cap the length and normalise a cloning reference."""
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    if len(x) == 0:
        return x
    threshold = max(1e-4, float(np.max(np.abs(x))) * 0.02)
    active = np.where(np.abs(x) > threshold)[0]
    if len(active):
        pad = int(sr * 0.15)
        x = x[max(0, active[0] - pad): min(len(x), active[-1] + pad)]
    x = x[: int(sr * max_seconds)]
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak > 0:
        x = x / peak * 0.9
    return x.astype(np.float32)
