"""Settings: defaults < .env file < real environment variables < command-line flags.

Every path is resolved against the repository folder, never the current working directory, so the
server behaves the same whether it is started from a launcher, a service manager or another folder.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
BUNDLED_VOICES_DIR = ROOT / "voices" / "presets"


def load_dotenv(path: Path) -> None:
    """Minimal .env reader (KEY=VALUE, # comments, optional quotes). Never overrides real env vars."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name, "")
    if not value:
        return default
    return value.lower() not in ("0", "false", "no", "off")


def _resolve(path_value: str) -> Path:
    p = Path(os.path.expanduser(path_value))
    return p if p.is_absolute() else (ROOT / p).resolve()


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8808
    device: str = "auto"                 # auto | cuda | cuda:N | mps | cpu
    model_dir: Path = ROOT / "models" / "voxcpm2"
    data_dir: Path = ROOT / "data"
    api_key: str = ""
    cors_origins: List[str] = field(default_factory=list)
    optimize: bool = False               # torch.compile (CUDA only; slow first start, faster afterwards)
    default_voice: str = "vox-aura-studio"
    cfg_value: float = 2.0
    inference_timesteps: int = 10
    max_chars_per_piece: int = 300       # long text is generated sentence-group by sentence-group
    max_input_chars: int = 20000
    open_browser: bool = False
    preload: bool = True                 # load the model at start-up instead of on the first request

    @property
    def voices_dir(self) -> Path:
        return self.data_dir / "voices"

    @property
    def public_url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::", "") else self.host
        return f"http://{host}:{self.port}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m voxcpm_server",
        description="VoxCPM2 Voice Server: local OpenAI-compatible neural text-to-speech with a web UI.",
    )
    p.add_argument("--host", help="Interface to listen on (default 127.0.0.1; use 0.0.0.0 for your LAN)")
    p.add_argument("--port", type=int, help="Port (default 8808)")
    p.add_argument("--device", help="auto | cuda | cuda:0 | mps | cpu (default auto)")
    p.add_argument("--model-dir", help="Folder holding the VoxCPM2 weights (default ./models/voxcpm2)")
    p.add_argument("--data-dir", help="Folder for your voices and settings (default ./data)")
    p.add_argument("--api-key", help="Require this key (Authorization: Bearer <key>) for API calls")
    p.add_argument("--optimize", action="store_true", help="Enable torch.compile on CUDA (slower start, faster speech)")
    p.add_argument("--open", action="store_true", help="Open the web UI in your browser once the server is up")
    p.add_argument("--no-open", action="store_true", help="Never open the browser (overrides --open)")
    p.add_argument("--lazy", action="store_true", help="Load the model on the first request instead of at start-up")
    return p


def load_settings(argv: Optional[List[str]] = None) -> Settings:
    load_dotenv(ROOT / ".env")
    args, _unknown = build_parser().parse_known_args(argv)

    s = Settings(
        host=_env("VOXCPM_HOST", "127.0.0.1") or "127.0.0.1",
        port=_env_int("VOXCPM_PORT", 8808),
        device=(_env("VOXCPM_DEVICE", "auto") or "auto").lower(),
        model_dir=_resolve(_env("VOXCPM_MODEL_DIR", "models/voxcpm2") or "models/voxcpm2"),
        data_dir=_resolve(_env("VOXCPM_DATA_DIR", "data") or "data"),
        api_key=_env("VOXCPM_API_KEY"),
        cors_origins=[o.strip() for o in _env("VOXCPM_CORS_ORIGINS").split(",") if o.strip()],
        optimize=_env_bool("VOXCPM_OPTIMIZE", False),
        default_voice=_env("VOXCPM_DEFAULT_VOICE", "vox-aura-studio") or "vox-aura-studio",
        cfg_value=_env_float("VOXCPM_CFG", 2.0),
        inference_timesteps=_env_int("VOXCPM_STEPS", 10),
        max_chars_per_piece=max(80, _env_int("VOXCPM_MAX_CHARS_PER_PIECE", 300)),
        max_input_chars=max(500, _env_int("VOXCPM_MAX_INPUT_CHARS", 20000)),
        open_browser=_env_bool("VOXCPM_OPEN_BROWSER", False),
        preload=_env_bool("VOXCPM_PRELOAD", True),
    )
    if args.host:
        s.host = args.host
    if args.port:
        s.port = args.port
    if args.device:
        s.device = args.device.lower()
    if args.model_dir:
        s.model_dir = _resolve(args.model_dir)
    if args.data_dir:
        s.data_dir = _resolve(args.data_dir)
    if args.api_key:
        s.api_key = args.api_key.strip()
    if args.optimize:
        s.optimize = True
    if args.open:
        s.open_browser = True
    if args.no_open:
        s.open_browser = False
    if args.lazy:
        s.preload = False
    return s
