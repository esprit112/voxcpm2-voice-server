#!/usr/bin/env bash
# VoxCPM2 Voice Server - start (Linux / macOS).
#   ./start.sh                 start and open the web UI
#   ./start.sh --no-open       do not open a browser (servers, SSH sessions)
#   ./start.sh --device cpu    force CPU          ./start.sh --port 8809   another port
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
  echo "VoxCPM2 Voice Server is not installed yet. Run ./install-linux.sh or ./install-macos.sh first." >&2
  exit 1
fi
export PYTHONUNBUFFERED=1 PYTHONUTF8=1
open_flag="--open"
# No display (SSH / headless server): do not try to open a browser
if [ "$(uname -s)" = "Linux" ] && [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
  open_flag=""
fi
exec .venv/bin/python -m voxcpm_server ${open_flag} "$@"
