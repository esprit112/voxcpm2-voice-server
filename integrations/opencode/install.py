#!/usr/bin/env python3
"""Connect the VoxCPM2 Voice Server to OpenCode.

Adds a local MCP server named "voxcpm2" to the global OpenCode config (~/.config/opencode/opencode.json,
or opencode.jsonc if that is what you use). Both config layouts are handled:
    v1:  "mcp": { "voxcpm2": { "type": "local", "command": [...], "environment": {...}, "enabled": true } }
    v2:  "mcp": { "servers": { "voxcpm2": { "type": "local", "command": [...], "environment": {...} } } }

    python integrations/opencode/install.py
    python integrations/opencode/install.py --uninstall
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import SERVER_NAME, base_parser, finish_args, load_json, mcp_command, mcp_env, ok, save_json, say, server_check  # noqa: E402


def config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "opencode"
    for name in ("opencode.json", "opencode.jsonc"):
        if (base / name).exists():
            return base / name
    return base / "opencode.json"


def main(argv=None) -> int:
    p = base_parser("Connect the VoxCPM2 Voice Server to OpenCode")
    p.add_argument("--voice", help="voice for this agent (id or name)")
    args = finish_args(p.parse_args(argv))
    say("\n== OpenCode ==")
    path = config_path()
    data = load_json(path)
    data.setdefault("$schema", "https://opencode.ai/config.json")
    mcp = data.setdefault("mcp", {})
    v2 = isinstance(mcp.get("servers"), dict)
    table = mcp["servers"] if v2 else mcp

    if args.uninstall:
        table.pop(SERVER_NAME, None)
        save_json(path, data, args)
        ok("Removed the voxcpm2 MCP server from OpenCode.")
        return 0

    server_check(args)
    env = mcp_env(args)
    if args.voice:
        env["VOXCPM_VOICE"] = args.voice
    entry = {"type": "local", "command": mcp_command(), "environment": env}
    if not v2:
        entry["enabled"] = True
    table[SERVER_NAME] = entry
    save_json(path, data, args)
    say("\n  Done. Restart OpenCode. Try: \"use voxcpm2 to read the changelog aloud\".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
