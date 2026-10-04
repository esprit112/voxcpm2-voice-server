"""Voice library.

A voice is identified by a short id and is spoken from ONE reference recording, so its timbre stays
the same from clip to clip:

  * Preset voices ship with a reference in voices/presets/<id>.wav (+ .txt transcript). If a preset
    reference is missing, the model designs one from the preset's description on first use.
  * Designed voices are created from a natural-language description ("a warm, raspy old sailor").
  * Cloned voices are created from a short recording of a real person (with their consent!).

User voices live in <data>/voices/voices.json and <data>/voices/<id>.wav/.txt.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import BUNDLED_VOICES_DIR

PRESETS: List[Dict] = [
    {
        "id": "vox-aura-studio", "name": "Aura Studio", "gender": "female", "language": "en-US",
        "style": "Warm broadcast & narrative",
        "description": "Silky, natural conversational delivery with rich mid-range warmth.",
        "design": "A warm, friendly American woman in her thirties with a smooth, natural conversational voice, like a podcast host",
    },
    {
        "id": "vox-cortex-pro", "name": "Cortex Pro", "gender": "male", "language": "en-US",
        "style": "Analytical & technical",
        "description": "Measured, precise baritone with crisp articulation. Ideal for technical briefings.",
        "design": "A calm, analytical American man in his forties with a clear baritone voice and crisp, precise articulation",
    },
    {
        "id": "vox-zephyr-warm", "name": "Zephyr Warm", "gender": "neutral", "language": "en-US",
        "style": "Empathetic & conversational",
        "description": "Gentle, friendly and engaging. For companions, customer experiences and guided audio.",
        "design": "A gentle, friendly young adult with a soft, warm, androgynous voice and a caring, engaging conversational tone",
    },
    {
        "id": "vox-vesper-noir", "name": "Vesper Noir", "gender": "male", "language": "en-US",
        "style": "Raspy & intimate",
        "description": "Low gravel and slight vocal fry. Perfect for audiobooks, mystery and trailers.",
        "design": "A middle-aged American man with a low, raspy, intimate voice, like a film-noir narrator",
    },
    {
        "id": "vox-ember-dynamic", "name": "Ember Dynamic", "gender": "female", "language": "en-US",
        "style": "High energy & paced",
        "description": "Vibrant, fast-paced and expressive. For commercials, reels and tutorials.",
        "design": "An energetic young American woman with a bright, lively, fast-paced voice, like a commercial presenter",
    },
    {
        "id": "vox-solas-british", "name": "Solas British", "gender": "male", "language": "en-GB",
        "style": "Distinguished & academic",
        "description": "Crisp Received Pronunciation with elegant phrasing. For documentaries and literature.",
        "design": "A distinguished middle-aged British man with a crisp Received Pronunciation accent and an elegant documentary-narrator voice",
    },
    {
        "id": "vox-lyra-air", "name": "Lyra Air", "gender": "female", "language": "en-US",
        "style": "Soft-spoken",
        "description": "Airy, intimate and gentle. Designed for meditation and poetry.",
        "design": "A soft-spoken young American woman with an airy, intimate, gentle voice, calm like a meditation guide",
    },
    {
        "id": "vox-atlas-titan", "name": "Atlas Titan", "gender": "male", "language": "en-US",
        "style": "Authoritative & resonant",
        "description": "Deep, commanding presence with deliberate pacing.",
        "design": "An older American man with a very deep, resonant, authoritative voice and slow, commanding delivery",
    },
]

CALIBRATION_TEXT = (
    "Hello, this is my natural speaking voice. I read clearly and calmly, "
    "with steady pacing and a relaxed, friendly tone."
)

# OpenAI voice names, so clients that only know those still get a sensible voice
OPENAI_ALIASES = {
    "alloy": "vox-zephyr-warm", "ash": "vox-cortex-pro", "ballad": "vox-solas-british",
    "coral": "vox-ember-dynamic", "echo": "vox-vesper-noir", "fable": "vox-solas-british",
    "nova": "vox-aura-studio", "onyx": "vox-atlas-titan", "sage": "vox-lyra-air",
    "shimmer": "vox-lyra-air", "verse": "vox-cortex-pro",
}

_SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")


def is_safe_id(value: str) -> bool:
    return bool(_SAFE_ID.match(str(value or "")))


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")[:40] or "voice"
    return f"{slug}-{secrets.token_hex(3)}"


class VoiceLibrary:
    def __init__(self, voices_dir: Path, default_voice: str = "vox-aura-studio"):
        self.dir = Path(voices_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "voices.json"
        self.default_voice = default_voice
        self._lock = threading.RLock()
        self._custom: Dict[str, Dict] = {}
        self._hidden: set = set()
        self._load()

    # ---- persistence ---------------------------------------------------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        for v in data.get("voices", []) if isinstance(data, dict) else []:
            if isinstance(v, dict) and is_safe_id(v.get("id", "")):
                self._custom[v["id"]] = v
        self._hidden = {h for h in (data.get("hidden_presets", []) if isinstance(data, dict) else []) if is_safe_id(h)}

    def _save(self) -> None:
        payload = {"voices": list(self._custom.values()), "hidden_presets": sorted(self._hidden)}
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.index_path)

    # ---- queries -------------------------------------------------------------------------------
    def all(self) -> List[Dict]:
        with self._lock:
            presets = [dict(p, kind="preset") for p in PRESETS if p["id"] not in self._hidden]
            customs = sorted(self._custom.values(), key=lambda v: v.get("created", 0))
            voices = presets + [dict(v) for v in customs]
        for v in voices:
            v["has_reference"] = self.reference_path(v["id"]) is not None
            v["default"] = v["id"] == self.default_id(voices)
        return voices

    def default_id(self, voices: Optional[List[Dict]] = None) -> str:
        voices = voices if voices is not None else self.all_raw()
        ids = [v["id"] for v in voices]
        if self.default_voice in ids:
            return self.default_voice
        return ids[0] if ids else PRESETS[0]["id"]

    def all_raw(self) -> List[Dict]:
        with self._lock:
            return [p for p in PRESETS if p["id"] not in self._hidden] + list(self._custom.values())

    def get(self, voice_id: str) -> Optional[Dict]:
        with self._lock:
            found = None
            if voice_id in self._custom:
                found = dict(self._custom[voice_id])
            else:
                for p in PRESETS:
                    if p["id"] == voice_id:
                        found = dict(p, kind="preset")
                        break
        if found is not None:
            found["has_reference"] = self.reference_path(voice_id) is not None
        return found

    def resolve(self, wanted: Optional[str]) -> Dict:
        """Find a voice by id, name or OpenAI alias (case-insensitive); unknown names give the default."""
        key = str(wanted or "").strip()
        if key:
            exact = self.get(key)
            if exact:
                return exact
            folded = key.casefold()
            for v in self.all_raw():
                if folded in (v["id"].casefold(), v["name"].casefold()):
                    return self.get(v["id"]) or v
            alias = OPENAI_ALIASES.get(folded)
            if alias and self.get(alias):
                return self.get(alias)
        return self.get(self.default_id()) or dict(PRESETS[0], kind="preset")

    # ---- reference audio -----------------------------------------------------------------------
    def reference_path(self, voice_id: str) -> Optional[Path]:
        for folder in (self.dir, BUNDLED_VOICES_DIR):
            p = folder / f"{voice_id}.wav"
            if p.is_file() and p.stat().st_size > 1000:
                return p
        return None

    def reference_transcript(self, voice_id: str) -> Optional[str]:
        ref = self.reference_path(voice_id)
        if not ref:
            return None
        try:
            text = ref.with_suffix(".txt").read_text(encoding="utf-8").strip()
            return text or None
        except OSError:
            return None

    def save_reference(self, voice_id: str, wav_bytes: bytes, transcript: Optional[str]) -> Path:
        target = self.dir / f"{voice_id}.wav"
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(wav_bytes)
        os.replace(tmp, target)
        txt = target.with_suffix(".txt")
        if transcript and transcript.strip():
            txt.write_text(transcript.strip(), encoding="utf-8")
        elif txt.exists():
            txt.unlink()
        return target

    # ---- changes -------------------------------------------------------------------------------
    def add(self, *, name: str, kind: str, description: str = "", design: str = "", gender: str = "",
            language: str = "", voice_id: Optional[str] = None) -> Dict:
        name = " ".join(str(name or "").split())[:60] or "My voice"
        with self._lock:
            vid = voice_id if voice_id and is_safe_id(voice_id) else slugify(name)
            voice = {
                "id": vid, "name": name, "kind": kind, "gender": gender or "",
                "language": language or "", "style": "Designed" if kind == "designed" else "Cloned",
                "description": description[:400], "design": design[:400], "created": int(time.time()),
            }
            self._custom[vid] = voice
            self._save()
            return dict(voice)

    def update(self, voice_id: str, **fields) -> Optional[Dict]:
        with self._lock:
            v = self._custom.get(voice_id)
            if not v:
                return None
            for key in ("name", "description", "gender", "language"):
                if key in fields and fields[key] is not None:
                    v[key] = str(fields[key])[:400]
            self._save()
            return dict(v)

    def delete(self, voice_id: str) -> Tuple[bool, str]:
        with self._lock:
            if voice_id in self._custom:
                del self._custom[voice_id]
                for ext in (".wav", ".txt"):
                    p = self.dir / f"{voice_id}{ext}"
                    if p.exists():
                        p.unlink()
                self._save()
                return True, "deleted"
            if any(p["id"] == voice_id for p in PRESETS):
                if len(self.all_raw()) <= 1:
                    return False, "This is the last voice; create another one first."
                self._hidden.add(voice_id)
                self._save()
                return True, "hidden"
        return False, "Voice not found"

    def restore_presets(self) -> List[str]:
        with self._lock:
            restored = sorted(self._hidden)
            self._hidden.clear()
            self._save()
            return restored
