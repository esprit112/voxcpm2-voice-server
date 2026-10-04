#!/usr/bin/env bash
# =================================================================================================
#  VoxCPM2 Voice Server - macOS installer (Apple Silicon: M1 / M2 / M3 / M4 and newer)
#
#  Installs everything inside this folder, without admin rights and without touching other Pythons:
#    1. checks macOS version, chip, disk and memory
#    2. installs uv (a fast Python manager) into ~/.local/bin if needed
#    3. creates a private Python 3.11 environment in .venv
#    4. installs PyTorch with Metal (MPS) GPU support + the VoxCPM2 engine
#    5. downloads the VoxCPM2 model (~5 GB) into models/voxcpm2
#    6. writes .env (with the Apple GPU settings) and runs a health check
#    7. optional: start automatically at login (launchd)
#
#  Intel Macs are not supported: current PyTorch releases ship Apple Silicon builds only.
#  Safe to run again: it repairs/updates an existing install and skips finished steps.
#
#  Usage: ./install-macos.sh [options]
#    --cpu                    use the CPU instead of the Apple GPU
#    --skip-model             do not download the model now (the server fetches it on first start)
#    --model-dir PATH         keep the model somewhere else (e.g. an existing download)
#    --port N                 server port (default 8808)
#    --python X.Y             Python version for .venv (default 3.11)
#    --service                start the server automatically when you log in
#    --yes                    do not ask questions
#    -h | --help              this help
# =================================================================================================
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
LOG="$ROOT/install.log"
exec > >(tee -a "$LOG") 2>&1

FORCE_CPU=0; SKIP_MODEL=0; MODEL_DIR=""; PORT=""; PYVER="3.11"; SERVICE=0; YES=0
LABEL="io.github.voxcpm2.voiceserver"

c_cyan=$'\033[36m'; c_green=$'\033[32m'; c_yellow=$'\033[33m'; c_red=$'\033[31m'; c_mag=$'\033[35m'; c_off=$'\033[0m'
step() { printf '\n%s[%s/7] %s%s\n' "$c_cyan" "$1" "$2" "$c_off"; }
ok()   { printf '  %sOK%s   %s\n' "$c_green" "$c_off" "$1"; }
info() { printf '       %s\n' "$1"; }
warn() { printf '  %sWARN%s %s\n' "$c_yellow" "$c_off" "$1"; }
fail() {
  printf '\n  %sFAILED:%s %s\n' "$c_red" "$c_off" "$1"
  [ -n "${2:-}" ] && printf '  %sFix:%s    %s\n' "$c_yellow" "$c_off" "$2"
  printf '  Full log: %s\n' "$LOG"
  exit 1
}
ask() {
  [ "$YES" = 1 ] && return 0
  [ -t 0 ] || return 1
  local a; read -r -p "$1 [Y/n] " a || true
  [[ -z "$a" || "$a" =~ ^[Yy] ]]
}
trap 'fail "Unexpected error on line $LINENO." "See the log above; running the installer again usually resumes."' ERR

while [ $# -gt 0 ]; do
  case "$1" in
    --cpu) FORCE_CPU=1 ;;
    --skip-model) SKIP_MODEL=1 ;;
    --model-dir) MODEL_DIR="${2:?}"; shift ;;
    --port) PORT="${2:?}"; shift ;;
    --python) PYVER="${2:?}"; shift ;;
    --service) SERVICE=1 ;;
    --yes|-y) YES=1 ;;
    -h|--help) sed -n '2,30p' "$0" | sed 's/^#  \{0,1\}//'; exit 0 ;;
    *) fail "Unknown option: $1" "Run ./install-macos.sh --help" ;;
  esac
  shift
done

printf '%s=================================================================\n' "$c_mag"
printf '  VoxCPM2 Voice Server - macOS installer\n  Folder: %s\n' "$ROOT"
printf '=================================================================%s\n' "$c_off"

# -------------------------------------------------------------------------------------------------
step 1 "Checking this Mac"
[ "$(uname -s)" = "Darwin" ] || fail "This is the macOS installer." "Use install-linux.sh on Linux or install-windows.bat on Windows."

MACOS="$(sw_vers -productVersion)"
MAJOR="${MACOS%%.*}"
ok "macOS $MACOS"
[ "$MAJOR" -lt 13 ] && warn "macOS 13 Ventura or newer is recommended for the Apple GPU (MPS)."

CHIP="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo unknown)"
if [ "$(uname -m)" != "arm64" ]; then
  if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
    fail "This Terminal is running under Rosetta (Intel emulation) on an Apple Silicon Mac." \
         "Quit Terminal, open Finder > Applications > Utilities > Terminal > Get Info, untick 'Open using Rosetta', then run again."
  fi
  fail "This is an Intel Mac ($CHIP). PyTorch no longer publishes Intel macOS builds, so VoxCPM2 cannot run natively." \
       "Use an Apple Silicon Mac, or run the server on a Windows/Linux machine and connect to it over the network."
fi
ok "Apple Silicon: $CHIP"

NEED_GB=15; { [ "$SKIP_MODEL" = 1 ] || [ -n "$MODEL_DIR" ]; } && NEED_GB=9
FREE_GB=$(df -Pk "$ROOT" | awk 'NR==2 {printf "%d", $4/1024/1024}')
[ "$FREE_GB" -lt "$NEED_GB" ] && fail "Only ${FREE_GB} GB free here; about ${NEED_GB} GB is needed." "Free some space or move this folder."
ok "${FREE_GB} GB free disk space"

RAM_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
if [ "$RAM_GB" -lt 16 ]; then
  warn "${RAM_GB} GB unified memory. 16 GB or more is recommended; with 8 GB, close other apps while generating."
else
  ok "${RAM_GB} GB unified memory"
fi
command -v curl >/dev/null 2>&1 || fail "curl is missing (it ships with macOS)." "Install the Xcode command line tools: xcode-select --install"
case "$PYVER" in
  3.10|3.11|3.12|3.13) ;;
  *) warn "Python $PYVER is not supported (some VoxCPM2 dependencies have no ready-made packages for it). Using 3.11."; PYVER="3.11" ;;
esac

# -------------------------------------------------------------------------------------------------
step 2 "Installing uv (Python manager)"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  info "Downloading uv from astral.sh ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh || fail "Could not install uv." "Check your connection, or: brew install uv"
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || fail "uv was installed but is not on PATH." "Open a new Terminal window and run the installer again."
fi
ok "uv $(uv --version | awk '{print $2}')"

# -------------------------------------------------------------------------------------------------
step 3 "Creating the Python $PYVER environment (.venv)"
PY="$ROOT/.venv/bin/python"
if [ -x "$PY" ]; then
  ok "Existing .venv found - reusing it"
else
  uv venv .venv --python "$PYVER" || fail "Creating the Python environment failed." "Delete the .venv folder and run again."
  ok "Created .venv"
fi

# -------------------------------------------------------------------------------------------------
step 4 "Installing PyTorch (Metal/MPS) and the VoxCPM2 engine (a few GB, be patient)"
# On macOS the standard PyPI wheels include Apple GPU (MPS) support.
uv pip install --python "$PY" torch torchaudio || fail "Installing PyTorch failed." "Check your internet connection and run again."
uv pip install --python "$PY" -r requirements.txt || fail "Installing the VoxCPM2 engine failed." "Look above for the package that failed."
CHECK=$("$PY" -c "import torch, voxcpm, soundfile; print(torch.__version__, 'MPS' if torch.backends.mps.is_available() else 'CPU')" 2>&1) \
  || fail "The installed packages do not import: $CHECK" "Delete .venv and run the installer again."
ok "PyTorch $CHECK"
[[ "$CHECK" == *MPS* ]] || warn "The Apple GPU (MPS) is not available to PyTorch; the CPU will be used (slower). Update macOS."

# -------------------------------------------------------------------------------------------------
step 5 "Writing settings (.env)"
if [ ! -f .env ]; then cp .env.example .env; ok "Created .env from .env.example"; else ok ".env already exists - keeping your settings"; fi
set_env() {
  local key="$1" val="$2" tmp
  tmp="$(mktemp)"
  if grep -qE "^[[:space:]]*#?[[:space:]]*$key=" .env; then
    awk -v k="$key" -v v="$val" '$0 ~ "^[[:space:]]*#?[[:space:]]*" k "=" { print k "=" v; next } { print }' .env > "$tmp"
  else
    cat .env > "$tmp"; printf '%s=%s\n' "$key" "$val" >> "$tmp"
  fi
  mv "$tmp" .env
}
set_env PYTORCH_ENABLE_MPS_FALLBACK 1
[ -n "$PORT" ] && { set_env VOXCPM_PORT "$PORT"; ok "Port set to $PORT"; }
[ -n "$MODEL_DIR" ] && { set_env VOXCPM_MODEL_DIR "$MODEL_DIR"; ok "Model folder set to $MODEL_DIR"; }
[ "$FORCE_CPU" = 1 ] && { set_env VOXCPM_DEVICE cpu; ok "Device set to CPU"; }

# -------------------------------------------------------------------------------------------------
step 6 "Downloading the VoxCPM2 model (~5 GB, one time)"
if [ "$SKIP_MODEL" = 1 ]; then
  info "Skipped (--skip-model). The server downloads it on first start, or run: .venv/bin/python scripts/download_model.py"
else
  "$PY" scripts/download_model.py || warn "The download did not finish. Run the installer again (it resumes) or just start the server."
fi

# -------------------------------------------------------------------------------------------------
step 7 "Final checks"
chmod +x start.sh doctor.sh integrate.sh install-macos.sh 2>/dev/null || true
# Files downloaded with a browser carry a quarantine flag that can block the scripts
xattr -dr com.apple.quarantine "$ROOT" 2>/dev/null || true
"$PY" scripts/doctor.py --quick || true

if [ "$SERVICE" = 1 ]; then
  AGENTS="$HOME/Library/LaunchAgents"; mkdir -p "$AGENTS" "$ROOT/data"
  PLIST="$AGENTS/$LABEL.plist"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>-m</string><string>voxcpm_server</string><string>--no-open</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>$ROOT/data/server.log</string>
  <key>StandardErrorPath</key><string>$ROOT/data/server.log</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  ok "The server now starts at login (log: data/server.log)."
  info "Undo: launchctl bootout gui/$(id -u)/$LABEL && rm $PLIST"
fi

P="${PORT:-8808}"
printf '\n%s=================================================================\n' "$c_green"
printf '  Installed!\n'
printf '  Start:        ./start.sh\n'
printf '  Web UI:       http://127.0.0.1:%s\n' "$P"
printf '  OpenAI API:   http://127.0.0.1:%s/v1\n' "$P"
printf '  AI agents:    ./integrate.sh\n'
printf '  Problems?     ./doctor.sh\n'
printf '=================================================================%s\n' "$c_off"
trap - ERR
if [ "$SERVICE" = 0 ] && ask "Start the server now?"; then exec ./start.sh; fi
