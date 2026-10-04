#!/usr/bin/env python3
"""Connect the VoxCPM2 Voice Server to desktop MCP clients.

    --client claude-desktop   Claude Desktop app       (claude_desktop_config.json, "mcpServers")
    --client cursor           Cursor                   (~/.cursor/mcp.json, "mcpServers")
    --client windsurf         Windsurf                 (~/.codeium/windsurf/mcp_config.json, "mcpServers")
    --client vscode           VS Code / Copilot agent  (user mcp.json, "servers")
    --client lmstudio         LM Studio                (~/.lmstudio/mcp.json, "mcpServers")
    --client all              every client found on this computer (default)

    python integrations/mcp_clients/install.py
    python integrations/mcp_clients/install.py --client cursor --uninstall
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import (IS_MAC, IS_WIN, SERVER_NAME, base_parser, finish_args, load_json, mcp_command, mcp_env, ok,  # noqa: E402
                    save_json, say, server_check, warn)

H = Path.home()
APPDATA = Path(os.environ.get("APPDATA", H / "AppData" / "Roaming"))


def app_config_dir(name: str) -> Path:
    if IS_WIN:
        return APPDATA / name
    if IS_MAC:
        return H / "Library" / "Application Support" / name
    return Path(os.environ.get("XDG_CONFIG_HOME", H / ".config")) / name


CLIENTS = {
    "claude-desktop": ("Claude Desktop", lambda: app_config_dir("Claude") / "claude_desktop_config.json", "mcpServers",
                       lambda: app_config_dir("Claude")),
    "cursor": ("Cursor", lambda: H / ".cursor" / "mcp.json", "mcpServers", lambda: H / ".cursor"),
    "windsurf": ("Windsurf", lambda: H / ".codeium" / "windsurf" / "mcp_config.json", "mcpServers",
                 lambda: H / ".codeium" / "windsurf"),
    "vscode": ("VS Code", lambda: app_config_dir("Code") / "User" / "mcp.json", "servers",
               lambda: app_config_dir("Code") / "User"),
    "lmstudio": ("LM Studio", lambda: H / ".lmstudio" / "mcp.json", "mcpServers", lambda: H / ".lmstudio"),
}


def main(argv=None) -> int:
    p = base_parser("Connect the VoxCPM2 Voice Server to desktop MCP clients")
    p.add_argument("--client", choices=[*CLIENTS, "all"], default="all")
    p.add_argument("--voice", help="voice for these agents (id or name)")
    args = finish_args(p.parse_args(argv))
    say("\n== Desktop MCP clients ==")
    if not args.uninstall:
        server_check(args)
    env = mcp_env(args)
    if args.voice:
        env["VOXCPM_VOICE"] = args.voice
    cmd = mcp_command()
    chosen = list(CLIENTS) if args.client == "all" else [args.client]
    touched = 0
    for key in chosen:
        label, path_fn, root_key, marker_fn = CLIENTS[key]
        path = path_fn()
        if args.client == "all" and not marker_fn().exists():
            continue  # not installed here
        data = load_json(path)
        servers = data.setdefault(root_key, {})
        if args.uninstall:
            if SERVER_NAME in servers:
                servers.pop(SERVER_NAME)
                save_json(path, data, args)
                ok(f"{label}: removed")
            continue
        entry = {"command": cmd[0], "args": cmd[1:], "env": env}
        if key == "vscode":
            entry = {"type": "stdio", **entry}
        servers[SERVER_NAME] = entry
        save_json(path, data, args)
        ok(f"{label}: added 'voxcpm2' - restart {label} to load it")
        touched += 1
    if not args.uninstall and touched == 0:
        warn("None of the supported clients was found. Use --client NAME to write its config anyway.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
