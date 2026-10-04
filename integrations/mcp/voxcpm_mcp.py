#!/usr/bin/env python3
"""VoxCPM2 Voice Server - MCP server (stdio, standard library only).

Gives any MCP-capable AI agent (Claude Code, Codex CLI, Gemini CLI, OpenCode, Claude Desktop, Cursor,
Windsurf, VS Code, LM Studio, ...) a voice:

  speak                 say text out loud on this computer's speakers
  text_to_speech_file   save speech to an audio file and return its path
  list_voices           the voices available on the server
  design_voice          create a new voice from a description
  voice_server_status   is the server up, which device, which model state

Environment:
  VOXCPM_URL            server address (default http://127.0.0.1:8808)
  VOXCPM_API_KEY        server API key, if one is set
  VOXCPM_VOICE          default voice for this agent (id or name)
  VOXCPM_OUTPUT_DIR     where text_to_speech_file saves audio (default <server folder>/outputs)
  VOXCPM_AUTOSTART      1 (default) = start the local server automatically when it is not running
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
URL = os.environ.get("VOXCPM_URL", "http://127.0.0.1:8808").rstrip("/")
API_KEY = os.environ.get("VOXCPM_API_KEY", "").strip()
DEFAULT_VOICE = os.environ.get("VOXCPM_VOICE", "").strip()
OUTPUT_DIR = Path(os.environ.get("VOXCPM_OUTPUT_DIR", str(ROOT / "outputs"))).expanduser()
AUTOSTART = os.environ.get("VOXCPM_AUTOSTART", "1").strip().lower() not in ("0", "false", "no", "off")
PROTOCOL = "2025-06-18"
VERSION = "1.0.0"
IS_WIN = os.name == "nt"

EMOTIONS = ["neutral", "happy", "cheerful", "excited", "calm", "sad", "tired", "angry", "frustrated", "concerned",
            "fearful", "surprised", "empathetic", "authoritative", "dramatic", "whisper"]

TOOLS = [
    {
        "name": "speak",
        "description": ("Say text out loud through this computer's speakers with a local neural voice. Use it when "
                        "the user asks you to read, say or announce something. Inline markers change delivery mid-"
                        "text: [happy] [sad] [excited] [calm] [whisper] [pause:500ms] [normal]."),
        "inputSchema": {"type": "object", "properties": {
            "text": {"type": "string", "description": "What to say (plain prose works best; no markdown)."},
            "voice": {"type": "string", "description": "Voice id or name (see list_voices). Optional."},
            "emotion": {"type": "string", "enum": EMOTIONS, "description": "Overall delivery. Optional."},
            "speed": {"type": "number", "minimum": 0.5, "maximum": 2.0, "description": "1.0 = normal."},
            "wait": {"type": "boolean", "description": "Wait until playback has finished (default false)."},
        }, "required": ["text"]},
    },
    {
        "name": "text_to_speech_file",
        "description": "Generate speech and save it as an audio file (narration, voice-overs, podcasts). Returns the file path.",
        "inputSchema": {"type": "object", "properties": {
            "text": {"type": "string"},
            "voice": {"type": "string"},
            "emotion": {"type": "string", "enum": EMOTIONS},
            "speed": {"type": "number", "minimum": 0.5, "maximum": 2.0},
            "format": {"type": "string", "enum": ["mp3", "wav", "flac", "opus"], "description": "Default mp3."},
            "output_path": {"type": "string", "description": "Full path of the file to write. Optional."},
        }, "required": ["text"]},
    },
    {
        "name": "list_voices",
        "description": "List the voices available on the local VoxCPM2 Voice Server.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "design_voice",
        "description": ("Create a new voice from a description (age, gender, accent, texture, mood), e.g. 'an old "
                        "Scottish sea captain, gravelly and warm'. Takes a few seconds on a GPU. Returns the new voice id."),
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string"}, "description": {"type": "string"},
        }, "required": ["name", "description"]},
    },
    {
        "name": "voice_server_status",
        "description": "Check whether the VoxCPM2 Voice Server is running and ready.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def log(msg: str) -> None:
    sys.stderr.write(f"[voxcpm2-mcp] {msg}\n")
    sys.stderr.flush()


# ---- server access -------------------------------------------------------------------------------
def http(path: str, payload=None, timeout: float = 900, method=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json", "User-Agent": f"voxcpm2-mcp/{VERSION}"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    req = urllib.request.Request(URL + path, data=data, headers=headers, method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            err = json.loads(body).get("error")
            body = err.get("message") if isinstance(err, dict) else (err or body)
        except ValueError:
            pass
        raise RuntimeError(f"Voice server error {exc.code}: {body[:300]}") from None


def health(timeout: float = 3):
    try:
        body, _ = http("/health", timeout=timeout)
        return json.loads(body)
    except Exception:  # noqa: BLE001
        return None


_start_lock = threading.Lock()


def ensure_server() -> None:
    """Start the local server in the background if it is not answering (and wait for it to listen)."""
    if health() is not None:
        return
    host = URL.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0].strip("[]")
    if not AUTOSTART or host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError(f"The VoxCPM2 Voice Server is not running at {URL}. Start it first.")
    with _start_lock:
        if health() is not None:
            return
        py = ROOT / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")
        if not py.exists():
            raise RuntimeError(f"The voice server is not installed in {ROOT} (no .venv). Run its installer.")
        port = URL.rsplit(":", 1)[-1].split("/")[0]
        log_path = ROOT / "data" / "server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log(f"starting the voice server ({py} -m voxcpm_server --port {port})")
        flags = 0
        if IS_WIN:
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0x8) | 0x08000000
        with open(log_path, "ab") as lf:
            subprocess.Popen([str(py), "-m", "voxcpm_server", "--no-open", "--port", port], cwd=str(ROOT),
                             stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                             creationflags=flags, start_new_session=not IS_WIN)
        deadline = time.time() + 60
        while time.time() < deadline:
            if health() is not None:
                return
            time.sleep(1)
        raise RuntimeError(f"Started the voice server but it did not answer within 60s. See {log_path}.")


def synthesize(args: dict, fmt: str) -> bytes:
    text = str(args.get("text") or "").strip()
    if not text:
        raise ValueError("'text' is empty.")
    ensure_server()
    body = {"model": "voxcpm2", "input": text, "response_format": fmt}
    voice = args.get("voice") or DEFAULT_VOICE
    if voice:
        body["voice"] = voice
    if args.get("emotion"):
        body["emotion"] = args["emotion"]
    if args.get("speed"):
        body["speed"] = float(args["speed"])
    audio, _ = http("/v1/audio/speech", body)
    return audio


# ---- playback ------------------------------------------------------------------------------------
def play_command(path: str):
    if IS_WIN:
        ps = f"(New-Object Media.SoundPlayer '{path}').PlaySync()"
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps]
    if sys.platform == "darwin":
        return ["afplay", path]
    for cmd in (["pw-play", path], ["paplay", path], ["aplay", "-q", path],
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path], ["mpv", "--no-video", "--really-quiet", path]):
        if shutil.which(cmd[0]):
            return cmd
    return None


def play(path: str, wait: bool) -> str:
    cmd = play_command(path)
    if not cmd:
        return "No audio player found (install pipewire, pulseaudio-utils, alsa-utils or ffmpeg)."
    flags = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags)
    if wait:
        proc.wait(timeout=3600)
    return ""


def wav_seconds(data: bytes) -> float:
    try:
        import io
        import wave

        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:  # noqa: BLE001
        return 0.0


# ---- tools ---------------------------------------------------------------------------------------
def tool_speak(args: dict) -> str:
    audio = synthesize(args, "wav")
    fd, path = tempfile.mkstemp(prefix="voxcpm2_", suffix=".wav")
    with os.fdopen(fd, "wb") as fh:
        fh.write(audio)
    problem = play(path, bool(args.get("wait")))
    secs = wav_seconds(audio)
    if problem:
        return f"Generated {secs:.1f}s of speech ({path}) but could not play it: {problem}"
    return f"Speaking {secs:.1f}s of audio now." if not args.get("wait") else f"Spoke {secs:.1f}s of audio."


def tool_file(args: dict) -> str:
    fmt = str(args.get("format") or "mp3").lower()
    audio = synthesize(args, fmt)
    out = args.get("output_path")
    if out:
        path = Path(out).expanduser()
        if not path.suffix:
            path = path.with_suffix(f".{fmt}")
    else:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUTPUT_DIR / f"speech_{time.strftime('%Y%m%d_%H%M%S')}.{fmt}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio)
    return f"Saved {len(audio) / 1024:.0f} KB of {fmt.upper()} audio to {path.resolve()}"


def tool_voices(_args: dict) -> str:
    ensure_server()
    body, _ = http("/api/voices", timeout=30)
    data = json.loads(body)
    lines = [f"- {v['id']}: {v['name']} ({v.get('kind')}, {v.get('gender') or 'any'}, {v.get('language') or '?'})"
             f"{' [default]' if v.get('default') else ''} - {v.get('description', '')}" for v in data["voices"]]
    return "Available voices:\n" + "\n".join(lines)


def tool_design(args: dict) -> str:
    ensure_server()
    body, _ = http("/api/voices/design", {"name": args.get("name"), "description": args.get("description")})
    v = json.loads(body)["voice"]
    return f"Created voice '{v['name']}' with id '{v['id']}'. Use it with speak(voice='{v['id']}')."


def tool_status(_args: dict) -> str:
    h = health()
    if not h:
        return f"The VoxCPM2 Voice Server is not running at {URL}." + (" It will be started automatically on the next speak call." if AUTOSTART else "")
    return (f"Server {URL}: {h['status']} on {h.get('device_label') or h.get('device')}, "
            f"{h.get('stats', {}).get('requests', 0)} requests served. Default voice: {h.get('default_voice')}.")


HANDLERS = {"speak": tool_speak, "text_to_speech_file": tool_file, "list_voices": tool_voices,
            "design_voice": tool_design, "voice_server_status": tool_status}


# ---- JSON-RPC over stdio -------------------------------------------------------------------------
_out_lock = threading.Lock()


def send(msg: dict) -> None:
    line = json.dumps(msg, ensure_ascii=False)
    with _out_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def handle(msg: dict) -> None:
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:  # notification (initialized, cancelled, ...)
        return
    try:
        if method == "initialize":
            requested = params.get("protocolVersion") or PROTOCOL
            result = {"protocolVersion": requested, "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "voxcpm2-voice-server", "version": VERSION},
                      "instructions": "Local neural text-to-speech. Call speak to talk out loud; text_to_speech_file to save audio."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            fn = HANDLERS.get(name)
            if not fn:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool: {name}"}})
                return
            try:
                text, is_error = fn(params.get("arguments") or {}), False
            except Exception as exc:  # noqa: BLE001 - tool errors are reported to the model, not as protocol errors
                text, is_error = str(exc), True
            result = {"content": [{"type": "text", "text": text}], "isError": is_error}
        elif method in ("resources/list", "prompts/list"):
            result = {method.split("/")[0]: []}
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}})
            return
        send({"jsonrpc": "2.0", "id": mid, "result": result})
    except Exception as exc:  # noqa: BLE001
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(exc)}})


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        sys.stdin.reconfigure(encoding="utf-8")
    log(f"ready (server {URL})")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        # Long generations run in threads so ping/list requests are still answered meanwhile
        if msg.get("method") == "tools/call":
            threading.Thread(target=handle, args=(msg,), daemon=True).start()
        else:
            handle(msg)


if __name__ == "__main__":
    main()
