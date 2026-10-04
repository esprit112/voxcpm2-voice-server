"""Entry point: python -m voxcpm_server [--host H] [--port P] [--device D] [--open] ..."""

from __future__ import annotations

import errno
import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser

# Windows consoles default to cp1252; never crash on a voice name or log line
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

# voxcpm prints a tqdm bar per generation step; keep the console readable unless asked for
if os.environ.get("VOXCPM_PROGRESS", "0").strip().lower() in ("0", "false", "no", "off", ""):
    os.environ.setdefault("TQDM_DISABLE", "1")

# Deprecation notices from torch/voxcpm internals (torch.jit.script, weight_norm) are not actionable for users
import warnings  # noqa: E402

warnings.filterwarnings("ignore", category=FutureWarning)

from . import __version__  # noqa: E402
from .api import serve  # noqa: E402
from .config import load_settings  # noqa: E402
from .engine import Engine, preflight_summary  # noqa: E402
from .voices import VoiceLibrary  # noqa: E402


def already_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=2) as resp:
            return "status" in json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    settings = load_settings(sys.argv[1:])
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    library = VoiceLibrary(settings.voices_dir, settings.default_voice)
    engine = Engine(settings, library)
    url = settings.public_url

    try:
        httpd = serve(settings, engine, library)
    except OSError as exc:
        in_use = exc.errno in (errno.EADDRINUSE, 10048, 10013) or "in use" in str(exc).lower()
        if in_use and already_running(url):
            print(f"VoxCPM2 Voice Server is already running at {url} - nothing to do.")
            if settings.open_browser:
                webbrowser.open(url)
            return 0
        if in_use:
            print(f"Port {settings.port} is used by another program. Start with --port 8809 "
                  f"(or set VOXCPM_PORT in .env).", file=sys.stderr)
            return 2
        raise

    exposed = settings.host not in ("127.0.0.1", "localhost", "::1")
    print("=" * 66)
    print(f"  VoxCPM2 Voice Server {__version__}")
    print(f"  Web UI     {url}")
    print(f"  OpenAI API {url}/v1   (POST /v1/audio/speech)")
    print(f"  {preflight_summary()}")
    print(f"  Model      {settings.model_dir}")
    print(f"  Voices     {settings.voices_dir}")
    if exposed:
        print(f"  Listening on {settings.host} - reachable from other machines.")
        if not settings.api_key:
            print("  WARNING: no VOXCPM_API_KEY set; anyone on your network can use this server.")
    print("  Press Ctrl+C to stop.")
    print("=" * 66, flush=True)

    if settings.preload:
        engine.start_loading()
    if settings.open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    worker = threading.Thread(target=httpd.serve_forever, name="http", daemon=True)
    worker.start()
    try:
        while worker.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping VoxCPM2 Voice Server...", flush=True)
    finally:
        httpd.shutdown()
        httpd.server_close()
    # Do not wait for a generation in progress; the process is exiting anyway
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
