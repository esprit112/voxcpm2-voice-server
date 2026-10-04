#!/usr/bin/env bash
# Give your AI agents a voice (Hermes, OpenClaw, Claude Code, Codex, Gemini CLI, OpenCode, Cursor, ...).
#   ./integrate.sh                       menu
#   ./integrate.sh claude-code codex     specific runtimes
#   ./integrate.sh --list                what is detected
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
if [ ! -x .venv/bin/python ]; then
  echo "The server is not installed yet. Run ./install-linux.sh or ./install-macos.sh first." >&2
  exit 1
fi
exec .venv/bin/python integrations/install.py "$@"
