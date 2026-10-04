"""Emotion markers: square-bracket tags inside the text that steer delivery from that point on.

    "Morning was fine. [tired] Today was long. [happy] But I got the job! [pause:500ms] [whisper] Don't tell."

Rules
  * A style marker ([sad], [tired], [excited, speaking quickly] ...) applies until the next style marker.
  * [neutral] forces plain delivery; [normal] / [reset] / a closing tag such as [/sad] returns to the
    emotion chosen for the whole request.
  * [pause] / [pause:500ms] / [pause:1.5s] / [breath] insert silence and leave the style alone.
  * Any other text in brackets becomes a free-form style instruction, e.g. [slow and mysterious].

VoxCPM2 takes one style instruction per generation, so text is cut into pieces at the markers. Long
pieces are further split at sentence boundaries so the model never runs out of generation length.
Only the standard library is used (unit-testable without torch).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

KNOWN_EMOTIONS = {
    "happy": "happy", "joyful": "happy",
    "cheerful": "cheerful", "upbeat": "cheerful",
    "excited": "excited", "energetic": "excited", "thrilled": "excited",
    "sad": "sad", "sorrowful": "sad", "melancholic": "sad",
    "tired": "tired", "exhausted": "tired", "weary": "tired", "sleepy": "tired",
    "angry": "angry", "furious": "angry", "mad": "angry",
    "calm": "calm", "relaxed": "calm", "soothing": "calm",
    "frustrated": "frustrated",
    "concerned": "concerned", "worried": "concerned",
    "fearful": "fearful", "scared": "fearful", "afraid": "fearful",
    "surprised": "surprised", "shocked": "surprised",
    "empathetic": "empathetic",
    "authoritative": "authoritative",
    "dramatic": "dramatic",
    "whisper": "whisper", "whispering": "whisper", "whispered": "whisper",
}

# Delivery instructions. VoxCPM2 follows directions about pace, loudness and pitch far more reliably
# than bare emotion words, so the important ones are phrased as directions.
EMOTION_STYLES = {
    "happy": "happy and joyful, bright smiling voice",
    "cheerful": "cheerful and upbeat, smiling voice",
    "excited": "speaking very fast and loud, high pitched, bright and full of energy",
    "sad": "speaking very slowly and quietly, low pitched, weak and trembling",
    "tired": "tired and weary, slow, low energy, sighing and drawn out",
    "angry": "shouting loudly, harsh, fast and aggressive",
    "calm": "speaking slowly and softly, low pitched, smooth and gentle",
    "frustrated": "frustrated and tense, clipped and irritated",
    "concerned": "concerned and caring, gentle and attentive",
    "fearful": "speaking fast in a trembling, shaky, breathless voice",
    "surprised": "surprised and astonished, higher pitch, with wide rising intonation",
    "empathetic": "warm and empathetic, tender and reassuring",
    "authoritative": "firm and authoritative, confident and decisive",
    "dramatic": "dramatic and theatrical, with suspenseful pauses",
    "whisper": "whispering softly",
}

RESET_WORDS = {"normal", "reset", "default", "end"}
NEUTRAL_WORDS = {"neutral", "none", "plain"}
UNSUPPORTED_TAGS = {"emphasis", "/emphasis", "laugh"}
TAG_PATTERN = re.compile(r"\[([^\[\]\n]{1,80})\]")
PAUSE_PATTERN = re.compile(r"^pause(?:\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|seconds?)?)?$")
MAX_PAUSE_MS = 5000
_HAS_SPEECH = re.compile(r"[^\W_]", re.UNICODE)  # any letter or digit, in any script


@dataclass
class Piece:
    text: str
    emotion: str = "neutral"          # key of EMOTION_STYLES, or "neutral"
    style: Optional[str] = None       # free-form instruction from an unknown marker
    pause_before_ms: int = 0
    marked: bool = False              # came from an explicit marker


@dataclass
class Plan:
    pieces: List[Piece] = field(default_factory=list)
    trailing_pause_ms: int = 0
    has_markers: bool = False


def has_speech(text: str) -> bool:
    return bool(_HAS_SPEECH.search(text or ""))


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"\s+([,.;:!?])", r"\1", text).strip()


def parse_pause(tag: str) -> Optional[int]:
    m = PAUSE_PATTERN.match(tag)
    if not m:
        return None
    if not m.group(1):
        return 400
    n = float(m.group(1))
    unit = m.group(2)
    ms = n * 1000 if unit and unit != "ms" else n
    return max(0, min(MAX_PAUSE_MS, int(round(ms))))


def style_instruction(raw: str) -> str:
    cleaned = re.sub(r"\s+", " ", re.sub(r"[()]", " ", raw)).strip()
    return cleaned if " " in cleaned else f"{cleaned} tone of voice, sounding {cleaned}"


def normalize_emotion(value: Optional[str]) -> str:
    key = str(value or "").strip().lower()
    if not key or key in NEUTRAL_WORDS or key in RESET_WORDS or key == "auto":
        return "neutral"
    return KNOWN_EMOTIONS.get(key, key)


def split_sentences(text: str) -> List[str]:
    parts = re.findall(r"[^.!?\u2026\u3002\uff01\uff1f]+(?:[.!?\u2026\u3002\uff01\uff1f]+[\"'\u201d\u2019)\]]*|$)", text or "")
    return [p.strip() for p in parts if has_speech(p)]


def chunk_text(text: str, max_chars: int) -> List[str]:
    """Group sentences into chunks of at most ~max_chars; very long sentences are cut at commas/spaces."""
    text = _tidy(text)
    if len(text) <= max_chars:
        return [text] if has_speech(text) else []
    chunks: List[str] = []
    current = ""
    for sentence in split_sentences(text) or [text]:
        pieces = [sentence]
        if len(sentence) > max_chars:
            pieces = _split_long(sentence, max_chars)
        for s in pieces:
            if current and len(current) + 1 + len(s) > max_chars:
                chunks.append(current)
                current = s
            else:
                current = f"{current} {s}".strip()
    if current:
        chunks.append(current)
    return [c for c in chunks if has_speech(c)]


def _split_long(sentence: str, max_chars: int) -> List[str]:
    out: List[str] = []
    rest = sentence
    while len(rest) > max_chars:
        window = rest[:max_chars]
        cut = max(window.rfind(", "), window.rfind("; "), window.rfind(": "))
        if cut < max_chars // 3:
            cut = window.rfind(" ")
        if cut < max_chars // 3:
            cut = max_chars - 1
        out.append(rest[: cut + 1].strip())
        rest = rest[cut + 1:].strip()
    if rest:
        out.append(rest)
    return out


def plan_text(text: str, base_emotion: str = "neutral", max_chars: int = 300) -> Plan:
    """Cut text into generation pieces at markers and sentence-group boundaries."""
    text = str(text or "")
    base = normalize_emotion(base_emotion)
    plan = Plan()
    pending_pause = 0
    current = {"emotion": None, "style": None, "marked": False}
    buffer = []

    def flush() -> None:
        nonlocal pending_pause
        segment = _tidy("".join(buffer))
        buffer.clear()
        if not has_speech(segment):
            return
        emotion = current["emotion"] if current["marked"] else base
        for i, chunk in enumerate(chunk_text(segment, max_chars)):
            plan.pieces.append(Piece(
                text=chunk,
                emotion=emotion or "neutral",
                style=current["style"],
                pause_before_ms=pending_pause if i == 0 else 0,
                marked=bool(current["marked"]),
            ))
        pending_pause = 0

    last = 0
    for match in TAG_PATTERN.finditer(text):
        raw = match.group(1).strip()
        tag = raw.lower()
        buffer.append(text[last:match.start()])
        last = match.end()
        if not raw:
            continue
        plan.has_markers = True
        pause = parse_pause(tag)
        if pause is not None or tag == "breath":
            flush()
            pending_pause = min(MAX_PAUSE_MS, pending_pause + (pause if pause is not None else 250))
            continue
        if tag in UNSUPPORTED_TAGS:
            continue
        flush()
        if tag in RESET_WORDS or tag.startswith("/"):
            current = {"emotion": None, "style": None, "marked": False}
        elif tag in NEUTRAL_WORDS:
            current = {"emotion": "neutral", "style": None, "marked": True}
        elif tag in KNOWN_EMOTIONS:
            current = {"emotion": KNOWN_EMOTIONS[tag], "style": None, "marked": True}
        else:
            current = {"emotion": "neutral", "style": style_instruction(raw), "marked": True}
    buffer.append(text[last:])
    flush()
    plan.trailing_pause_ms = pending_pause
    return plan


def strip_markers(text: str) -> str:
    """Text without any [tags] (for logs and for clients that just want the words)."""
    return _tidy(TAG_PATTERN.sub(" ", text or ""))
