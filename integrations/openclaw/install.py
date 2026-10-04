#!/usr/bin/env python3
"""Connect the VoxCPM2 Voice Server to OpenClaw.

OpenClaw's TTS supports any OpenAI-compatible endpoint ("Non-default values are treated as
OpenAI-compatible TTS endpoints, so custom model and voice names are accepted"). This sets:

    messages.tts.provider = "openai"
    messages.tts.providers.openai = { baseUrl, apiKey, model: "voxcpm2", voice }      (current schema)
    messages.tts.openai           = { ... }                                          (older releases)

It uses `openclaw config set` when the CLI is available (validated by OpenClaw itself) and falls back to
editing ~/.openclaw/openclaw.json (with a backup) otherwise.

    python integrations/openclaw/install.py                   # configure, keep auto-TTS as it is
    python integrations/openclaw/install.py --auto always     # speak every reply
    python integrations/openclaw/install.py --uninstall
Inside a chat you can also toggle speech with /tts always  |  /tts off.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import base_parser, fail, fetch_voices, finish_args, load_json, ok, run, save_json, say, server_check, warn  # noqa: E402


def config_path() -> Path:
    if os.environ.get("OPENCLAW_CONFIG_PATH"):
        return Path(os.environ["OPENCLAW_CONFIG_PATH"]).expanduser()
    return Path.home() / ".openclaw" / "openclaw.json"


def main(argv=None) -> int:
    p = base_parser("Connect the VoxCPM2 Voice Server to OpenClaw")
    p.add_argument("--voice", help="voice id (default: the server's default voice)")
    p.add_argument("--auto", choices=["always", "off", "inbound", "tagged"], help="set messages.tts.auto")
    p.add_argument("--legacy-schema", action="store_true", help="write messages.tts.openai (older OpenClaw)")
    args = finish_args(p.parse_args(argv))
    say("\n== OpenClaw ==")
    cli = shutil.which("openclaw")
    path = config_path()
    if not cli and not path.exists():
        return fail("OpenClaw was not found (no 'openclaw' command and no ~/.openclaw/openclaw.json). "
                    "Install it first: https://docs.openclaw.ai")

    voices = fetch_voices(args.server_url, args.api_key)
    voice = args.voice or next((v["voice_id"] for v in voices if v.get("default")), "vox-aura-studio")
    provider_cfg = {"baseUrl": f"{args.server_url}/v1", "apiKey": args.api_key or "local-voxcpm2",
                    "model": "voxcpm2", "voice": voice}

    if args.uninstall:
        if cli:
            for key in ("messages.tts.providers.openai", "messages.tts.openai"):
                run(["openclaw", "config", "unset", key], args, check=False)
            run(["openclaw", "config", "unset", "messages.tts.provider"], args, check=False)
        else:
            data = load_json(path)
            tts = data.get("messages", {}).get("tts", {})
            tts.pop("openai", None)
            (tts.get("providers") or {}).pop("openai", None)
            if tts.get("provider") == "openai":
                tts.pop("provider")
            save_json(path, data, args)
        ok("OpenClaw no longer uses the VoxCPM2 Voice Server.")
        return 0

    server_check(args)
    if cli:
        ok(f"OpenClaw CLI: {cli}")
        run(["openclaw", "config", "set", "messages.tts.provider", "openai"], args)
        keys = ["messages.tts.openai"] if args.legacy_schema else ["messages.tts.providers.openai", "messages.tts.openai"]
        done = False
        for key in keys:
            proc = run(["openclaw", "config", "set", key, json.dumps(provider_cfg), "--strict-json", "--merge"], args, check=False)
            if proc.returncode == 0:
                ok(f"{key} -> {provider_cfg['baseUrl']} (voice {voice})")
                done = True
                break
            warn(f"{key} was not accepted by this OpenClaw version; trying the older schema...")
        if not done:
            return fail("OpenClaw rejected both config layouts. Set it in the UI: Settings > Voice > OpenAI provider, "
                        f"base URL {provider_cfg['baseUrl']}, model voxcpm2, voice {voice}.")
        if args.auto:
            run(["openclaw", "config", "set", "messages.tts.auto", args.auto], args)
        run(["openclaw", "config", "validate"], args, check=False)
    else:
        warn("'openclaw' command not on PATH; editing the config file directly.")
        try:
            data = load_json(path)
        except ValueError as exc:
            return fail(f"Could not read {path}: {exc}. Run 'openclaw config set ...' instead (see the README).")
        tts = data.setdefault("messages", {}).setdefault("tts", {})
        tts["provider"] = "openai"
        if args.legacy_schema or ("openai" in tts and "providers" not in tts):
            tts["openai"] = {**tts.get("openai", {}), **provider_cfg}
        else:
            tts.setdefault("providers", {})["openai"] = {**tts.get("providers", {}).get("openai", {}), **provider_cfg}
        if args.auto:
            tts["auto"] = args.auto
        save_json(path, data, args)

    say("\n  Done. Restart the OpenClaw gateway (openclaw restart) so it picks up the change.")
    say("  Speak every reply: /tts always   |   only when asked: /tts off and use the tts tool.")
    say("  Cloud chat apps (Telegram etc.) get Opus voice notes; the server encodes them itself.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
