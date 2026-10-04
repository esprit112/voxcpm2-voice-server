#!/usr/bin/env python3
"""End-to-end check of a running VoxCPM2 Voice Server (standard library only).

    python scripts/smoke_test.py                 # quick: health, voices, one sentence
    python scripts/smoke_test.py --full          # every endpoint: formats, markers, streaming, design, clone
    python scripts/smoke_test.py --url http://127.0.0.1:8808 --api-key KEY --out ./smoke_out

Exit code 0 = everything passed.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = []


def req(base, path, payload=None, key="", method=None, timeout=900):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    r = urllib.request.Request(base + path, data=data, headers=headers, method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def check(name, ok, detail=""):
    ok = bool(ok)
    RESULTS.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail else ''}", flush=True)
    return ok


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("VOXCPM_URL", "http://127.0.0.1:8808"))
    ap.add_argument("--api-key", default=os.environ.get("VOXCPM_API_KEY", ""))
    ap.add_argument("--out", default=str(ROOT / "smoke_out"))
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--wait", type=int, default=1200, help="seconds to wait for the model to become ready")
    a = ap.parse_args()
    base, key, out = a.url.rstrip("/"), a.api_key, Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Testing {base}")
    t0 = time.time()
    status = None
    while time.time() - t0 < a.wait:
        try:
            _, _, body = req(base, "/health", timeout=5)
            h = json.loads(body)
            status = h["status"]
            if status == "ready":
                break
            if status in ("error", "no_model"):
                break
            print(f"  ...model is {status} ({int(time.time() - t0)}s)", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  ...server not answering yet ({exc.__class__.__name__})", flush=True)
        time.sleep(5)
    if not check("model ready", status == "ready", f"{status}" + (f" on {h.get('device_label')}" if status else "")):
        print(f"  error: {h.get('error') if status else 'server unreachable'}")
        return 1

    code, _, body = req(base, "/v1/audio/voices", key=key)
    voices = json.loads(body).get("voices", []) if code == 200 else []
    check("voice list", code == 200 and len(voices) > 0, f"{len(voices)} voices")
    voice = voices[0]["voice_id"] if voices else "vox-aura-studio"

    t = time.time()
    code, hdr, audio = req(base, "/v1/audio/speech", {"model": "voxcpm2", "voice": voice, "response_format": "wav",
                                                      "input": "Hello! This is a quick test of the voice server."}, key)
    ok = code == 200 and audio[:4] == b"RIFF"
    secs = wav_seconds(audio) if ok else 0
    elapsed = time.time() - t
    check("speech (wav)", ok and secs > 1.0, f"{secs:.1f}s audio in {elapsed:.1f}s (RTF {elapsed / max(secs, .01):.2f})")
    if ok:
        (out / "quick.wav").write_bytes(audio)

    if not a.full:
        return summary(out)

    for fmt, magic in (("mp3", (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")), ("flac", (b"fLaC",)), ("opus", (b"OggS",)), ("pcm", None)):
        code, hdr, data = req(base, "/v1/audio/speech", {"voice": voice, "input": "Format check.", "response_format": fmt}, key)
        used = hdr.get("X-Audio-Format", "?")
        good = code == 200 and len(data) > 1000 and (magic is None or data.startswith(magic))
        check(f"format {fmt}", good, f"{len(data)} bytes, served as {used}")
        if good:
            (out / f"format.{used if used != 'pcm' else 'pcm'}").write_bytes(data)

    code, hdr, data = req(base, "/v1/audio/speech", {"voice": "nova", "response_format": "wav",
                                                     "input": "Calm start. [excited] Now with energy! [pause:500ms] [whisper] And a secret."}, key)
    check("emotion markers + OpenAI alias 'nova'", code == 200 and data[:4] == b"RIFF",
          f"{wav_seconds(data):.1f}s, voice {hdr.get('X-Voice-Name')}" if code == 200 else data[:200].decode(errors='replace'))
    if code == 200:
        (out / "markers.wav").write_bytes(data)

    long_text = " ".join(["This sentence is part of a longer passage that the server must split into pieces."] * 8)
    code, hdr, data = req(base, "/v1/audio/speech", {"voice": voice, "input": long_text, "response_format": "wav"}, key)
    check("long text chunking", code == 200 and wav_seconds(data) > 20, f"{wav_seconds(data):.1f}s" if code == 200 else str(code))

    t = time.time()
    r = urllib.request.Request(base + "/api/stream", data=json.dumps({"voice": voice, "input": "Streaming test, one two three."}).encode(),
                               headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})})
    first = None
    total = 0
    with urllib.request.urlopen(r, timeout=600) as resp:
        sr = int(resp.headers.get("X-Sample-Rate", "48000"))
        while True:
            chunk = resp.read(8192)
            if not chunk:
                break
            first = first or (time.time() - t)
            total += len(chunk)
    check("streaming", total > sr, f"first bytes after {first or 0:.2f}s, {total / 4 / sr:.1f}s audio")

    code, _, body = req(base, "/api/voices/design", {"name": "Smoke Test Voice",
                                                     "description": "A friendly middle-aged woman with a clear, bright voice"}, key)
    designed = json.loads(body).get("voice", {}) if code == 200 else {}
    check("design voice", code == 200 and designed.get("has_reference"), designed.get("id", body[:200].decode(errors="replace")))

    sample = audio if audio[:4] == b"RIFF" else None
    if sample:
        code, _, body = req(base, "/api/voices/clone", {"name": "Smoke Clone", "audio_base64": base64.b64encode(sample).decode(),
                                                        "transcript": "Hello! This is a quick test of the voice server."}, key)
        cloned = json.loads(body).get("voice", {}) if code == 200 else {}
        check("clone voice", code == 200 and cloned.get("id"), cloned.get("id", body[:200].decode(errors="replace")))
        if cloned.get("id"):
            code, _, data = req(base, "/v1/audio/speech", {"voice": cloned["id"], "input": "Now speaking as the clone.", "response_format": "wav"}, key)
            check("speak with clone", code == 200 and data[:4] == b"RIFF")
            if code == 200:
                (out / "clone.wav").write_bytes(data)
            code, _, _ = req(base, f"/api/voices/{cloned['id']}", key=key, method="DELETE")
            check("delete clone", code == 200)
    if designed.get("id"):
        code, _, _ = req(base, f"/api/voices/{designed['id']}", key=key, method="DELETE")
        check("delete designed", code == 200)

    code, _, _ = req(base, "/v1/audio/speech", {"voice": voice, "input": "   "}, key)
    check("empty input rejected", code == 400, str(code))
    code, _, _ = req(base, "/api/voices/..%2F..%2Fetc", key=key, method="DELETE")
    check("path traversal rejected", code in (400, 404), str(code))
    return summary(out)


def summary(out: Path) -> int:
    passed = sum(RESULTS)
    print(f"\n{passed}/{len(RESULTS)} checks passed. Audio written to {out}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
