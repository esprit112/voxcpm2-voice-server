#!/usr/bin/env python3
"""Claude Code "Stop" hook: read Claude's reply aloud with the VoxCPM2 Voice Server.

Claude Code pipes the hook event as JSON on stdin; on Stop it includes `last_assistant_message`.
The hook returns immediately (it hands the text to a detached copy of itself), always exits 0 so it can
never block Claude, and stops the previous reply's playback when a new one starts.

Settings come from hook_config.json next to this file (written by install.py) or the environment:
  VOXCPM_URL, VOXCPM_API_KEY, VOXCPM_VOICE
  VOXCPM_HOOK_MAX_CHARS   longest text to read (default 600; longer replies are cut at a sentence)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))

# Settings written by install.py (server URL, API key, voice); real environment variables win
try:
    for _k, _v in json.loads((Path(__file__).resolve().parent / "hook_config.json").read_text(encoding="utf-8")).items():
        os.environ.setdefault(str(_k), str(_v))
except (OSError, ValueError):
    pass

MAX_CHARS = int(os.environ.get("VOXCPM_HOOK_MAX_CHARS", "600") or 600)
PID_FILE = Path(tempfile.gettempdir()) / "voxcpm2_claude_hook.pid"


def speakable(text: str) -> str:
    """Turn a markdown reply into something pleasant to listen to."""
    text = re.sub(r"```.*?```", " (code omitted) ", text, flags=re.S)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " a link ", text)
    text = re.sub(r"^\s{0,3}(#{1,6}|[-*+]|\d+\.|>)\s+", "", text, flags=re.M)
    text = re.sub(r"^\|.*\|$", " ", text, flags=re.M)              # tables
    text = re.sub(r"[*_~]{1,3}([^*_~]+)[*_~]{1,3}", r"\1", text)
    text = re.sub(r"\[[^\]]{0,80}\]", " ", text)                   # stray brackets would act as emotion markers
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_CHARS:
        cut = text[:MAX_CHARS]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        text = cut[: end + 1] if end > MAX_CHARS // 3 else cut.rsplit(" ", 1)[0] + "..."
    return text


def stop_previous() -> None:
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=5)
        else:
            os.killpg(pid, 15)
    except Exception:  # noqa: BLE001
        pass


def speak_now(text_file: str) -> None:
    """Detached worker: synthesize and play, blocking until playback ends."""
    import voxcpm_mcp as mcp

    text = Path(text_file).read_text(encoding="utf-8")
    try:
        os.remove(text_file)
    except OSError:
        pass
    mcp.tool_speak({"text": text, "wait": True})


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--speak-file":
        try:
            speak_now(sys.argv[2])
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"voxcpm2 hook: {exc}\n")
        return 0
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if event.get("stop_hook_active"):
        return 0
    text = speakable(str(event.get("last_assistant_message") or ""))
    if len(text) < 2:
        return 0
    fd, path = tempfile.mkstemp(prefix="voxcpm2_hook_", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    stop_previous()
    flags = (0x00000008 | 0x00000200 | 0x08000000) if os.name == "nt" else 0
    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--speak-file", path],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags, start_new_session=os.name != "nt", env=os.environ.copy())
    try:
        PID_FILE.write_text(str(proc.pid))
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - a hook must never break Claude Code
        pass
    sys.exit(0)
