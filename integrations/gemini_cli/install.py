#!/usr/bin/env python3
"""Connect the VoxCPM2 Voice Server to Google's Gemini CLI.

Adds "voxcpm2" to mcpServers in ~/.gemini/settings.json (user scope).

    python integrations/gemini_cli/install.py
    python integrations/gemini_cli/install.py --uninstall
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import SERVER_NAME, base_parser, finish_args, load_json, mcp_command, mcp_env, ok, save_json, say, server_check  # noqa: E402

SETTINGS = Path.home() / ".gemini" / "settings.json"


def main(argv=None) -> int:
    p = base_parser("Connect the VoxCPM2 Voice Server to Gemini CLI")
    p.add_argument("--voice", help="voice for this agent (id or name)")
    p.add_argument("--trust", action="store_true", help="let Gemini call the voice tools without confirming each time")
    args = finish_args(p.parse_args(argv))
    say("\n== Gemini CLI ==")
    data = load_json(SETTINGS)
    servers = data.setdefault("mcpServers", {})
    if args.uninstall:
        servers.pop(SERVER_NAME, None)
        if not servers:
            data.pop("mcpServers")
        save_json(SETTINGS, data, args)
        ok("Removed the voxcpm2 MCP server from Gemini CLI.")
        return 0
    server_check(args)
    cmd = mcp_command()
    env = mcp_env(args)
    if args.voice:
        env["VOXCPM_VOICE"] = args.voice
    servers[SERVER_NAME] = {"command": cmd[0], "args": cmd[1:], "env": env, "timeout": 900000, "trust": bool(args.trust)}
    save_json(SETTINGS, data, args)
    say("\n  Done. Restart Gemini CLI; /mcp lists 'voxcpm2'. Try: \"say good morning out loud\".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
