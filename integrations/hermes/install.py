#!/usr/bin/env python3
"""Connect the VoxCPM2 Voice Server to Hermes Agent.

Default ("plugin" mode): installs a native Hermes TTS provider plugin (voice picker in Settings, auto-starts
the server, Opus voice messages) into $HERMES_HOME/plugins/voxcpm2_server and selects it as the TTS provider.

"openai" mode: no plugin; points Hermes's built-in OpenAI TTS provider at this server instead.

    python integrations/hermes/install.py                 # plugin mode
    python integrations/hermes/install.py --mode openai   # built-in OpenAI provider
    python integrations/hermes/install.py --keep-provider # install but do not switch tts.provider
    python integrations/hermes/install.py --uninstall
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import (IS_WIN, ROOT, ask, base_parser, fail, fetch_voices, finish_args, ok, run, say,  # noqa: E402
                    server_check, warn)

PLUGIN_ID = "voxcpm2_server"
PLUGIN_SRC = Path(__file__).resolve().parent / "plugin"


def hermes_home() -> Path:
    if os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"]).expanduser()
    if IS_WIN:
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "hermes"
        if local.exists():
            return local
    return Path.home() / ".hermes"


def yaml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # JSON strings are valid YAML double-quoted strings


def main(argv=None) -> int:
    p = base_parser("Connect the VoxCPM2 Voice Server to Hermes Agent")
    p.add_argument("--mode", choices=["plugin", "openai"], default="plugin")
    p.add_argument("--keep-provider", action="store_true", help="do not change tts.provider")
    p.add_argument("--voice", help="voice to use (id or name)")
    args = finish_args(p.parse_args(argv))
    say("\n== Hermes Agent ==")

    home = hermes_home()
    hermes = shutil.which("hermes")
    if not home.exists() and not hermes:
        return fail(f"Hermes Agent was not found (no {home} and no 'hermes' command). Install Hermes first: "
                    "https://hermes-agent.nousresearch.com/docs")
    ok(f"Hermes home: {home}" + ("" if hermes else "  ('hermes' command not on PATH - will edit files only)"))
    target = home / "plugins" / PLUGIN_ID

    if args.uninstall:
        if hermes:
            run(["hermes", "plugins", "disable", PLUGIN_ID], args, check=False)
        if target.exists() and not args.dry_run:
            shutil.rmtree(target)
            ok(f"removed {target}")
        warn("If tts.provider was set to voxcpm2_server, choose another voice provider in Hermes Settings > Voice.")
        return 0

    server_check(args)
    voices = fetch_voices(args.server_url, args.api_key)
    names = [v.get("name") or v["voice_id"] for v in voices]
    default = next((v.get("name") for v in voices if v.get("default")), names[0] if names else "Aura Studio")
    if args.voice:
        match = next((v for v in voices if args.voice.casefold() in (v["voice_id"].casefold(), (v.get("name") or "").casefold())), None)
        default = (match or {}).get("name") or args.voice

    if args.mode == "openai":
        if not hermes:
            return fail("The 'hermes' command is needed for --mode openai.")
        voice_id = next((v["voice_id"] for v in voices if v.get("name") == default), "vox-aura-studio")
        for key, value in (("tts.provider", "openai"), ("tts.openai.base_url", f"{args.server_url}/v1"),
                           ("tts.openai.model", "voxcpm2"), ("tts.openai.voice", voice_id),
                           ("tts.openai.api_key", args.api_key or "not-needed")):
            run(["hermes", "config", "set", key, value], args)
        ok("Hermes now uses the server through its built-in OpenAI TTS provider. "
           "(A warning about unknown keys base_url/api_key is expected and harmless.)")
        return 0

    # ---- plugin mode ----
    template = (PLUGIN_SRC / "plugin.yaml.template").read_text(encoding="utf-8")
    manifest = (template.replace("__VOICE_CHOICES__", "[" + ", ".join(yaml_str(n) for n in names) + "]")
                .replace('"__DEFAULT_VOICE__"', yaml_str(default))
                .replace('"__SERVER_URL__"', yaml_str(args.server_url))
                .replace('"__SERVER_FOLDER__"', yaml_str(str(ROOT))))
    if args.dry_run:
        say(f"  (dry run) would copy the plugin to {target} with plugin.yaml:\n{manifest}")
    else:
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PLUGIN_SRC / "__init__.py", target / "__init__.py")
        (target / "plugin.yaml").write_text(manifest, encoding="utf-8")
        ok(f"plugin installed: {target}  ({len(names)} voices, default '{default}')")

    if hermes:
        run(["hermes", "plugins", "enable", PLUGIN_ID], args, check=False)
        if not args.keep_provider and ask("Make VoxCPM2 Voice Server Hermes's text-to-speech provider now?", args):
            run(["hermes", "config", "set", "tts.provider", PLUGIN_ID], args)
            ok("tts.provider = voxcpm2_server")
    else:
        warn(f"Enable it in Hermes: Capabilities > Plugins > VoxCPM2 Voice Server, then Settings > Voice > provider "
             f"'{PLUGIN_ID}'.")

    say("\n  Done. Restart Hermes (or start a new session) to load the plugin.")
    say("  Change the voice: Capabilities > Plugins > VoxCPM2 Voice Server > gear.")
    say("  Created new voices? Run this installer again to refresh the voice list.")
    say("  Tip: Hermes speaks its replies when voice output is switched on (Settings > Voice).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
