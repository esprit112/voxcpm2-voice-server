#!/usr/bin/env bash
# Diagnose the VoxCPM2 Voice Server install and print fixes (Linux / macOS).
cd "$(dirname "$0")" || exit 1
if [ -x ".venv/bin/python" ]; then
  exec .venv/bin/python scripts/doctor.py "$@"
fi
echo "Not installed yet - run ./install-linux.sh or ./install-macos.sh first." >&2
exit 1
