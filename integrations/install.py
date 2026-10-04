#!/usr/bin/env python3
"""Give your AI agents a voice: one menu for every supported runtime.

    python integrations/install.py                 # interactive menu (detects what is installed)
    python integrations/install.py claude-code codex
    python integrations/install.py all --yes
    python integrations/install.py hermes --uninstall
    python integrations/install.py --list

Extra options after the runtime names are passed to each installer (--voice, --server-url, --api-key,
--dry-run, --uninstall, --yes). Each runtime's own options: python integrations/<runtime>/install.py --help
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import IS_WIN, say  # noqa: E402

H = Path.home()

# key: (label, folder, how it connects, detection)
RUNTIMES = {
    "hermes": ("Hermes Agent", "hermes", "native TTS plugin",
               lambda: shutil.which("hermes") or (H / ".hermes").exists()
               or (Path(os.environ.get("LOCALAPPDATA", "")) / "hermes").exists()),
    "openclaw": ("OpenClaw", "openclaw", "OpenAI-compatible TTS",
                 lambda: shutil.which("openclaw") or (H / ".openclaw").exists()),
    "claude-code": ("Claude Code", "claude_code", "MCP tools + optional read-aloud hook",
                    lambda: shutil.which("claude") or (H / ".claude").exists()),
    "codex": ("OpenAI Codex CLI", "codex", "MCP tools", lambda: shutil.which("codex") or (H / ".codex").exists()),
    "gemini-cli": ("Gemini CLI", "gemini_cli", "MCP tools", lambda: shutil.which("gemini") or (H / ".gemini").exists()),
    "opencode": ("OpenCode", "opencode", "MCP tools",
                 lambda: shutil.which("opencode") or (H / ".config" / "opencode").exists()),
    "mcp-clients": ("Claude Desktop / Cursor / Windsurf / VS Code / LM Studio", "mcp_clients", "MCP tools",
                    lambda: True),
    "open-webui": ("Open WebUI", "open_webui", "OpenAI-compatible TTS", lambda: shutil.which("docker") or shutil.which("open-webui")),
}


def load(folder: str):
    spec = importlib.util.spec_from_file_location(f"vox_int_{folder}", HERE / folder / "install.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def menu() -> list:
    say("\nVoxCPM2 Voice Server - connect AI agent runtimes\n")
    keys = list(RUNTIMES)
    for i, key in enumerate(keys, 1):
        label, _, how, detect = RUNTIMES[key]
        found = "found" if detect() else "     "
        say(f"  {i}. [{found}] {label:56s} ({how})")
    say("\n  a. all runtimes marked 'found'      q. quit")
    choice = input("\nWhich ones? (e.g. 1 3 4, or a): ").strip().lower()
    if choice in ("q", ""):
        return []
    if choice in ("a", "all"):
        return [k for k in keys if RUNTIMES[k][3]()]
    picked = []
    for token in choice.replace(",", " ").split():
        if token.isdigit() and 1 <= int(token) <= len(keys):
            picked.append(keys[int(token) - 1])
        elif token in RUNTIMES:
            picked.append(token)
    return picked


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--list" in argv:
        for key, (label, _, how, detect) in RUNTIMES.items():
            say(f"  {key:12s} {'found' if detect() else '-    '}  {label} ({how})")
        return 0
    if argv and argv[0] in ("-h", "--help"):
        say(__doc__)
        return 0
    names = [a for a in argv if not a.startswith("-") and (a in RUNTIMES or a == "all")]
    rest = [a for a in argv if a not in names]
    if "all" in names:
        names = [k for k in RUNTIMES if RUNTIMES[k][3]()]
    if not names:
        if not sys.stdin.isatty():
            say("No runtime given. Example: python integrations/install.py claude-code --yes   (or --list)")
            return 2
        names = menu()
    failures = 0
    for key in names:
        try:
            rc = load(RUNTIMES[key][1]).main(rest)
        except SystemExit as exc:  # argparse errors
            rc = int(exc.code or 0)
        except Exception as exc:  # noqa: BLE001
            say(f"  [FAIL] {RUNTIMES[key][0]}: {exc}")
            rc = 1
        failures += 1 if rc else 0
    if names:
        say(f"\nFinished: {len(names) - failures} of {len(names)} succeeded.")
        say(f"Server must be running for the voices to work: {'start-windows.bat' if IS_WIN else './start.sh'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
