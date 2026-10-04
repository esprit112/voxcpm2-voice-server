"""Shared helpers for the agent-runtime integration installers (standard library only).

Every installer:
  * backs up any config file before changing it (<file>.bak-YYYYmmdd-HHMMSS)
  * is idempotent (running it twice gives the same result) and supports --uninstall and --dry-run
  * points the runtime at THIS server folder, using this folder's .venv Python
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
MCP_SCRIPT = ROOT / "integrations" / "mcp" / "voxcpm_mcp.py"
SERVER_NAME = "voxcpm2"          # name used for MCP server entries
IS_WIN = os.name == "nt"
IS_MAC = platform.system() == "Darwin"

sys.path.insert(0, str(ROOT))


# ---- console -------------------------------------------------------------------------------------
def say(msg: str = "") -> None:
    print(msg, flush=True)


def ok(msg: str) -> None:
    say(f"  [OK]   {msg}")


def warn(msg: str) -> None:
    say(f"  [WARN] {msg}")


def fail(msg: str) -> int:
    say(f"  [FAIL] {msg}")
    return 1


def ask(question: str, args: argparse.Namespace, default: bool = True) -> bool:
    if getattr(args, "yes", False):
        return default
    if not sys.stdin or not sys.stdin.isatty():
        return default
    hint = "[Y/n]" if default else "[y/N]"
    answer = input(f"{question} {hint} ").strip().lower()
    return default if not answer else answer.startswith("y")


# ---- this install --------------------------------------------------------------------------------
def venv_python() -> str:
    """The server's own Python (has nothing extra the MCP server needs, but is guaranteed to exist)."""
    candidates = [ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"]
    for c in candidates:
        if c.exists():
            return str(c)
    return sys.executable


def settings():
    from voxcpm_server.config import load_settings

    return load_settings([])


def default_server_url() -> str:
    try:
        return settings().public_url
    except Exception:  # noqa: BLE001
        return "http://127.0.0.1:8808"


def default_api_key() -> str:
    try:
        return settings().api_key
    except Exception:  # noqa: BLE001
        return ""


def health(url: str, timeout: float = 3.0) -> Optional[Dict]:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def fetch_voices(url: str, key: str = "") -> List[Dict]:
    req = urllib.request.Request(f"{url.rstrip('/')}/v1/audio/voices",
                                 headers={"Authorization": f"Bearer {key}"} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8")).get("voices", [])
    except Exception:  # noqa: BLE001
        from voxcpm_server.voices import PRESETS

        return [{"voice_id": p["id"], "name": p["name"], "default": i == 0} for i, p in enumerate(PRESETS)]


def mcp_env(args: argparse.Namespace) -> Dict[str, str]:
    env = {"VOXCPM_URL": args.server_url}
    if args.api_key:
        env["VOXCPM_API_KEY"] = args.api_key
    return env


def mcp_command() -> List[str]:
    return [venv_python(), str(MCP_SCRIPT)]


# ---- files ---------------------------------------------------------------------------------------
def backup(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    dest = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, dest)
    say(f"         backup: {dest}")
    return dest


def _strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas, leaving string contents alone."""
    out, i, n, in_str, esc = [], 0, len(text), False, False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
        elif text.startswith("//", i):
            while i < n and text[i] not in "\r\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(ch)
            i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def load_json(path: Path) -> Dict[str, Any]:
    """Read JSON or JSONC. A missing/empty file gives {}."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        data = json.loads(_strip_jsonc(text))
        warn(f"{path.name} contains comments; they will not survive this edit (a backup is kept).")
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return data


def save_json(path: Path, data: Dict[str, Any], args: argparse.Namespace) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if args.dry_run:
        say(f"  (dry run) would write {path}:\n{text}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    backup(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    ok(f"updated {path}")


def run(cmd: List[str], args: argparse.Namespace, check: bool = True) -> subprocess.CompletedProcess:
    shown = " ".join(f'"{c}"' if " " in c else c for c in cmd)
    if args.dry_run:
        say(f"  (dry run) would run: {shown}")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    say(f"  $ {shown}")
    exe = shutil.which(cmd[0]) or cmd[0]  # Windows needs the full path for .cmd/.bat shims
    proc = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.stdout.strip():
        say("    " + proc.stdout.strip().replace("\n", "\n    "))
    if proc.returncode != 0 and check and proc.stderr.strip():
        say("    " + proc.stderr.strip().replace("\n", "\n    "))
    return proc


def home() -> Path:
    return Path.home()


# ---- arguments -----------------------------------------------------------------------------------
def base_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--server-url", default=None, help="VoxCPM2 Voice Server address (default: from .env)")
    p.add_argument("--api-key", default=None, help="server API key, if you set VOXCPM_API_KEY")
    p.add_argument("--uninstall", action="store_true", help="remove the integration again")
    p.add_argument("--dry-run", action="store_true", help="show what would change without changing anything")
    p.add_argument("--yes", "-y", action="store_true", help="do not ask questions")
    return p


def finish_args(args: argparse.Namespace) -> argparse.Namespace:
    args.server_url = (args.server_url or default_server_url()).rstrip("/")
    args.api_key = args.api_key if args.api_key is not None else default_api_key()
    return args


def server_check(args: argparse.Namespace) -> None:
    h = health(args.server_url)
    if h:
        ok(f"VoxCPM2 Voice Server answers at {args.server_url} ({h.get('status')})")
    else:
        warn(f"No server at {args.server_url} right now. That is fine - start it later "
             f"({'start-windows.bat' if IS_WIN else './start.sh'}); the MCP tools can also start it on demand.")
