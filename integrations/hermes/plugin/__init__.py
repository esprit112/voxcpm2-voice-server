"""VoxCPM2 Voice Server: a text-to-speech provider for Hermes Agent.

Hermes hands the text to this plugin; the plugin asks the VoxCPM2 Voice Server on this computer
(http://127.0.0.1:8808 by default) to speak it and writes the audio file Hermes asked for. The server
encodes MP3 / Opus / FLAC / WAV itself, so no ffmpeg is needed. When the server is not running and
"auto_start" is on, the plugin starts it from the folder recorded at install time.

Settings (Capabilities > Plugins > VoxCPM2 Voice Server > gear; stored under plugins.entries.voxcpm2_server.settings):
    voice, emotion, server_url, auto_start, server_folder, audio_format, voice_messages, timeout_seconds, api_key

Only the Python standard library is used.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.tts_provider import TTSProvider

logger = logging.getLogger(__name__)

PROVIDER_NAME = "voxcpm2_server"
DISPLAY_NAME = "VoxCPM2 Voice Server"
DEFAULT_URL = "http://127.0.0.1:8808"
NATIVE_FORMATS = ("mp3", "opus", "flac", "wav")
_HERMES_ONLY_ENV = ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONEXECUTABLE",
                    "UV_PROJECT_ENVIRONMENT", "NODE_OPTIONS", "ELECTRON_RUN_AS_NODE")


class VoiceServerError(RuntimeError):
    """A failure with a message that is safe and useful to show in Hermes."""


def _clean_url(value: Any) -> str:
    url = str(value or "").strip().rstrip("/")
    return url if url.lower().startswith(("http://", "https://")) else DEFAULT_URL


def _manifest_defaults() -> Dict[str, Any]:
    try:
        from utils import fast_safe_load  # Hermes's YAML reader

        data = fast_safe_load((Path(__file__).parent / "plugin.yaml").read_text(encoding="utf-8")) or {}
        return {k: v.get("default") for k, v in (data.get("config_schema") or {}).items() if isinstance(v, dict)}
    except Exception:  # noqa: BLE001
        return {}


class VoxCPM2ServerProvider(TTSProvider):
    def __init__(self, ctx: Any = None) -> None:
        self._ctx = ctx
        self._defaults = _manifest_defaults()
        self._started_at = 0.0

    # ---- identity ------------------------------------------------------------------------------
    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return DISPLAY_NAME

    def get_setup_schema(self) -> Dict[str, Any]:
        return {"name": DISPLAY_NAME, "badge": "local", "tag": "Private neural voices generated on this computer",
                "env_vars": []}

    def is_available(self) -> bool:
        return True  # no network here; a stopped server is reported when speech is requested

    @property
    def voice_compatible(self) -> bool:
        return self._setting("voice_messages", False) is True

    # ---- settings ------------------------------------------------------------------------------
    def _setting(self, key: str, default: Any = None) -> Any:
        value = None
        if self._ctx is not None:
            try:
                value = self._ctx.get_config(key, None)
            except Exception:  # noqa: BLE001
                value = None
        if value is None or value == "":
            value = self._defaults.get(key)
        return default if value is None or value == "" else value

    def _url(self) -> str:
        return _clean_url(os.environ.get("VOXCPM_URL") or self._setting("server_url", DEFAULT_URL))

    def _key(self) -> str:
        key = ""
        try:
            from hermes_cli.config import get_env_value

            key = get_env_value("VOXCPM_API_KEY") or ""
        except Exception:  # noqa: BLE001
            pass
        return str(key or os.environ.get("VOXCPM_API_KEY") or self._setting("api_key", "") or "").strip()

    # ---- HTTP ----------------------------------------------------------------------------------
    def _request(self, path: str, payload: Optional[dict] = None, timeout: float = 10.0) -> bytes:
        headers = {"User-Agent": "Hermes-VoxCPM2-Server/1.0", "Accept": "*/*"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self._key():
            headers["Authorization"] = f"Bearer {self._key()}"
        req = urllib.request.Request(self._url() + path, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user's own server
                return resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                err = json.loads(detail).get("error")
                detail = err.get("message") if isinstance(err, dict) else str(err)
            except ValueError:
                pass
            if exc.code == 401:
                raise VoiceServerError("The VoxCPM2 Voice Server needs an API key: set it in this plugin's settings.") from exc
            raise VoiceServerError(f"VoxCPM2 Voice Server error {exc.code}: {detail[:300]}") from exc
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            raise VoiceServerError(f"The VoxCPM2 Voice Server is not reachable at {self._url()} ({exc}).") from exc

    def _running(self) -> bool:
        try:
            self._request("/health", timeout=2)
            return True
        except VoiceServerError:
            return False

    def _start_server(self) -> None:
        if self._setting("auto_start", True) is False or self._running():
            return
        host = self._url().split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0].strip("[]")
        folder = Path(str(self._setting("server_folder", "") or ""))
        if host not in ("127.0.0.1", "localhost", "::1") or not folder.is_dir():
            return
        if time.time() - self._started_at < 120:
            return  # a start is already under way
        py = folder / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not py.exists():
            logger.warning("VoxCPM2: no .venv in %s; run the server installer", folder)
            return
        env = {k: v for k, v in os.environ.items() if k.upper() not in _HERMES_ONLY_ENV}
        port = self._url().rsplit(":", 1)[-1].split("/")[0]
        log = open(folder / "data" / "server.log", "ab") if (folder / "data").is_dir() else subprocess.DEVNULL  # noqa: SIM115
        flags = (0x00000008 | 0x00000200 | 0x08000000) if os.name == "nt" else 0  # detached, new group, no window
        subprocess.Popen([str(py), "-m", "voxcpm_server", "--no-open", "--port", port], cwd=str(folder), env=env,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
                         start_new_session=os.name != "nt")
        self._started_at = time.time()
        logger.info("VoxCPM2: started the voice server from %s", folder)
        for _ in range(60):
            if self._running():
                return
            time.sleep(1)

    def warm(self) -> None:
        try:
            self._start_server()
        except Exception as exc:  # noqa: BLE001
            logger.debug("VoxCPM2 warm-up skipped: %s", exc)

    # ---- voices --------------------------------------------------------------------------------
    def _voices(self) -> List[Dict[str, Any]]:
        data = json.loads(self._request("/v1/audio/voices", timeout=5).decode("utf-8"))
        return [v for v in data.get("voices", []) if isinstance(v, dict) and v.get("voice_id")]

    def list_voices(self) -> List[Dict[str, Any]]:
        try:
            return [{"id": v["voice_id"], "display": v.get("name") or v["voice_id"], "language": v.get("language", ""),
                     "gender": v.get("gender", "")} for v in self._voices()]
        except Exception:  # noqa: BLE001
            return []

    def list_models(self) -> List[Dict[str, Any]]:
        return [{"id": "voxcpm2", "display": "VoxCPM2 (48 kHz, 30 languages)", "languages": []}]

    # ---- speech --------------------------------------------------------------------------------
    def synthesize(self, text: str, output_path: str, *, voice: Optional[str] = None, model: Optional[str] = None,
                   speed: Optional[float] = None, format: str = "mp3", **extra: Any) -> str:
        if not text or not text.strip():
            raise ValueError("There is no text to speak.")
        if not self._running():
            self._start_server()
        fmt = str(format or "mp3").lower()
        preferred = str(self._setting("audio_format", "") or "").lower()
        if preferred in NATIVE_FORMATS:
            fmt = preferred
        if self.voice_compatible or fmt == "ogg":
            fmt = "opus"
        if fmt not in NATIVE_FORMATS:
            fmt = "mp3"
        payload: Dict[str, Any] = {"model": "voxcpm2", "input": text, "response_format": fmt,
                                   "voice": voice or self._setting("voice", "") or None}
        emotion = str(extra.get("emotion") or self._setting("emotion", "neutral") or "neutral")
        if emotion.lower() not in ("", "neutral", "automatic"):
            payload["emotion"] = emotion.lower()
        if isinstance(speed, (int, float)) and not isinstance(speed, bool) and speed > 0:
            payload["speed"] = max(0.5, min(2.0, float(speed)))
        timeout = float(self._setting("timeout_seconds", 900) or 900)
        audio = self._request("/v1/audio/speech", payload, timeout=max(30.0, timeout))
        if not audio:
            raise VoiceServerError("The VoxCPM2 Voice Server returned no audio.")
        out = Path(output_path).with_suffix(".ogg" if fmt == "opus" else f".{fmt}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(audio)
        return str(out)


def register(ctx: Any) -> None:
    """Hermes plugin entry point."""
    ctx.register_tts_provider(VoxCPM2ServerProvider(ctx))


if __name__ == "__main__":  # pragma: no cover - quick manual test outside Hermes is not possible
    sys.exit("This file is a Hermes plugin; install it with integrations/hermes/install.py")
