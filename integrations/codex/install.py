#!/usr/bin/env python3
"""Connect the VoxCPM2 Voice Server to the OpenAI Codex CLI (also used by the Codex IDE extension and app).

Adds an [mcp_servers.voxcpm2] table to ~/.codex/config.toml (or $CODEX_HOME/config.toml) with a generous
tool timeout (speech can take a while on first use while the model loads).

    python integrations/codex/install.py
    python integrations/codex/install.py --uninstall
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import SERVER_NAME, backup, base_parser, finish_args, mcp_command, mcp_env, ok, say, server_check  # noqa: E402


def config_path() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser() / "config.toml"


def toml_literal(value: str) -> str:
    # TOML literal strings need no escaping (Windows paths stay readable); fall back to basic strings if needed
    if "'" not in value and "\n" not in value:
        return f"'{value}'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def remove_block(text: str) -> str:
    """Drop [mcp_servers.voxcpm2] and its sub-tables ([mcp_servers.voxcpm2.env] ...)."""
    lines, out, skipping = text.splitlines(keepends=True), [], False
    header = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")
    for line in lines:
        m = header.match(line)
        if m:
            name = m.group(1).replace('"', "").replace("'", "")
            skipping = name == f"mcp_servers.{SERVER_NAME}" or name.startswith(f"mcp_servers.{SERVER_NAME}.")
        if not skipping:
            out.append(line)
    return "".join(out).rstrip() + "\n" if "".join(out).strip() else ""


def main(argv=None) -> int:
    p = base_parser("Connect the VoxCPM2 Voice Server to OpenAI Codex CLI")
    p.add_argument("--voice", help="voice for this agent (id or name)")
    args = finish_args(p.parse_args(argv))
    say("\n== OpenAI Codex CLI ==")
    path = config_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    cleaned = remove_block(text)

    if args.uninstall:
        new = cleaned
    else:
        server_check(args)
        cmd = mcp_command()
        env = mcp_env(args)
        if args.voice:
            env["VOXCPM_VOICE"] = args.voice
        env_items = ", ".join(f"{k} = {toml_literal(v)}" for k, v in env.items())
        block = (f"\n[mcp_servers.{SERVER_NAME}]\n"
                 f"command = {toml_literal(cmd[0])}\n"
                 f"args = [{toml_literal(cmd[1])}]\n"
                 f"env = {{ {env_items} }}\n"
                 f"startup_timeout_sec = 20\n"
                 f"tool_timeout_sec = 900\n")
        new = (cleaned.rstrip() + "\n" if cleaned.strip() else "") + block

    if args.dry_run:
        say(f"  (dry run) {path} would become:\n{new}")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    backup(path)
    path.write_text(new, encoding="utf-8")
    ok(f"updated {path}")
    if args.uninstall:
        ok("Removed the voxcpm2 MCP server from Codex.")
    else:
        say("\n  Done. Start a new Codex session; `codex mcp list` shows 'voxcpm2'. Try: \"read this summary aloud\".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
