#!/usr/bin/env python3
"""Connect the VoxCPM2 Voice Server to Claude Code.

1. Registers the VoxCPM2 MCP server for all your projects (`claude mcp add --scope user`), giving Claude the
   tools speak, text_to_speech_file, list_voices, design_voice and voice_server_status.
2. Optional (--speak-replies): a Stop hook in ~/.claude/settings.json that reads every reply aloud.

    python integrations/claude_code/install.py
    python integrations/claude_code/install.py --speak-replies --voice vox-solas-british
    python integrations/claude_code/install.py --uninstall
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import (SERVER_NAME, ask, base_parser, finish_args, load_json, mcp_command, mcp_env, ok, run,  # noqa: E402
                    save_json, say, server_check, venv_python, warn)

HOOK = Path(__file__).resolve().parent / "speak_hook.py"
HOOK_CONFIG = Path(__file__).resolve().parent / "hook_config.json"
SETTINGS = Path.home() / ".claude" / "settings.json"
MARK = "speak_hook.py"


def hook_command() -> str:
    # Forward slashes work for bash (Claude Code's hook shell, also on Windows via Git Bash) and Windows alike
    py = venv_python().replace("\\", "/")
    return f'"{py}" "{HOOK.as_posix()}"'


def write_hook_config(args) -> None:
    cfg = {**mcp_env(args), **({"VOXCPM_VOICE": args.voice} if args.voice else {})}
    if args.dry_run:
        say(f"  (dry run) would write {HOOK_CONFIG}: {cfg}")
        return
    import json

    HOOK_CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def set_hook(args, enable: bool) -> None:
    data = load_json(SETTINGS)
    hooks = data.setdefault("hooks", {})
    groups = [g for g in hooks.get("Stop", []) if not any(MARK in str(h.get("command", "")) for h in g.get("hooks", []))]
    if enable:
        write_hook_config(args)
        groups.append({"hooks": [{"type": "command", "command": hook_command(), "timeout": 10}]})
    if groups:
        hooks["Stop"] = groups
    else:
        hooks.pop("Stop", None)
    if not hooks:
        data.pop("hooks", None)
    save_json(SETTINGS, data, args)


def main(argv=None) -> int:
    p = base_parser("Connect the VoxCPM2 Voice Server to Claude Code")
    p.add_argument("--speak-replies", action="store_true", help="read every Claude reply aloud (Stop hook)")
    p.add_argument("--voice", help="voice for this agent (id or name)")
    p.add_argument("--scope", choices=["user", "project", "local"], default="user")
    args = finish_args(p.parse_args(argv))
    say("\n== Claude Code ==")
    claude = shutil.which("claude")

    if args.uninstall:
        if claude:
            run(["claude", "mcp", "remove", SERVER_NAME, "--scope", args.scope], args, check=False)
        if SETTINGS.exists():
            set_hook(args, enable=False)
        ok("Removed the VoxCPM2 MCP server and the read-aloud hook.")
        return 0

    server_check(args)
    env = mcp_env(args)
    if args.voice:
        env["VOXCPM_VOICE"] = args.voice
    if claude:
        run(["claude", "mcp", "remove", SERVER_NAME, "--scope", args.scope], args, check=False)
        cmd = ["claude", "mcp", "add", "--scope", args.scope]
        for k, v in env.items():
            cmd += ["-e", f"{k}={v}"]
        cmd += [SERVER_NAME, "--", *mcp_command()]
        proc = run(cmd, args)
        if proc.returncode == 0:
            ok("MCP server 'voxcpm2' registered. In Claude Code, /mcp shows it; try: \"say hello out loud\".")
        else:
            warn("`claude mcp add` failed; add it by hand with the command shown above.")
    else:
        warn("The 'claude' command was not found. Install Claude Code, then run this again - or add it by hand:")
        envs = " ".join(f"-e {k}={v}" for k, v in env.items())
        say(f'    claude mcp add --scope user {envs} {SERVER_NAME} -- "{mcp_command()[0]}" "{mcp_command()[1]}"')

    if args.speak_replies or ask("Also read every Claude reply aloud (Stop hook)?", args, default=False):
        set_hook(args, enable=True)
        ok("Read-aloud hook installed. Turn it off with: --uninstall, or remove the Stop hook in /hooks.")
    say("\n  Restart Claude Code to load the changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
