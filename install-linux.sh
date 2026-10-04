#!/usr/bin/env bash
# =================================================================================================
#  VoxCPM2 Voice Server - Linux installer
#
#  Installs everything inside this folder, without root and without touching your system Python:
#    1. checks your distro, CPU, disk, memory and GPU (NVIDIA / AMD ROCm / Intel Arc)
#    2. installs uv (a fast Python manager) into ~/.local/bin if needed
#    3. creates a private Python 3.11 environment in .venv
#    4. installs PyTorch built for your hardware + the VoxCPM2 engine
#    5. downloads the VoxCPM2 model (~5 GB) into models/voxcpm2
#    6. writes .env and runs a health check
#    7. optional: systemd user service (start at boot) and an app-menu launcher
#
#  Safe to run again: it repairs/updates an existing install and skips finished steps.
#
#  Usage: ./install-linux.sh [options]
#    --cpu                    force the CPU build (no GPU)
#    --torch-backend NAME     auto (default), cpu, cu128, cu126, rocm6.4, xpu, ...
#    --skip-model             do not download the model now (the server fetches it on first start)
#    --model-dir PATH         keep the model somewhere else (e.g. an existing download)
#    --port N                 server port (default 8808)
#    --python X.Y             Python version for .venv (default 3.11)
#    --service                install + start a systemd user service
#    --desktop                add a launcher to your application menu
#    --yes                    do not ask questions
#    -h | --help              this help
# =================================================================================================
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
LOG="$ROOT/install.log"
exec > >(tee -a "$LOG") 2>&1

TORCH_BACKEND="auto"; FORCE_CPU=0; SKIP_MODEL=0; MODEL_DIR=""; PORT=""; PYVER="3.11"
SERVICE=0; DESKTOP=0; YES=0

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
    --torch-backend) TORCH_BACKEND="${2:?}"; shift ;;
    --skip-model) SKIP_MODEL=1 ;;
    --model-dir) MODEL_DIR="${2:?}"; shift ;;
    --port) PORT="${2:?}"; shift ;;
    --python) PYVER="${2:?}"; shift ;;
    --service) SERVICE=1 ;;
    --desktop) DESKTOP=1 ;;
    --yes|-y) YES=1 ;;
    -h|--help) sed -n '2,32p' "$0" | sed 's/^#  \{0,1\}//'; exit 0 ;;
    *) fail "Unknown option: $1" "Run ./install-linux.sh --help" ;;
  esac
  shift
done

printf '%s=================================================================\n' "$c_mag"
printf '  VoxCPM2 Voice Server - Linux installer\n  Folder: %s\n' "$ROOT"
printf '=================================================================%s\n' "$c_off"

# -------------------------------------------------------------------------------------------------
step 1 "Checking this computer"
[ "$(uname -s)" = "Linux" ] || fail "This is the Linux installer." "Use install-macos.sh on a Mac or install-windows.bat on Windows."
[ "$(id -u)" -eq 0 ] && warn "Running as root is not needed; your voices and config will belong to root."

if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  ok "${PRETTY_NAME:-Linux}"
fi
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64) ok "CPU architecture x86_64" ;;
  aarch64|arm64) warn "ARM64 Linux: CUDA works on NVIDIA Jetson/Grace only; other machines use the CPU build." ;;
  *) fail "Unsupported CPU architecture: $ARCH" "A 64-bit x86_64 or ARM64 system is required." ;;
esac
if grep -qi microsoft /proc/version 2>/dev/null; then
  info "WSL detected: NVIDIA GPUs work with the Windows NVIDIA driver (do not install a Linux driver inside WSL)."
fi

NEED_GB=15; { [ "$SKIP_MODEL" = 1 ] || [ -n "$MODEL_DIR" ]; } && NEED_GB=9
FREE_GB=$(df -Pk "$ROOT" | awk 'NR==2 {printf "%d", $4/1024/1024}')
[ "$FREE_GB" -lt "$NEED_GB" ] && fail "Only ${FREE_GB} GB free here; about ${NEED_GB} GB is needed (PyTorch ~4 GB, model ~5 GB)." "Free some space or move this folder."
ok "${FREE_GB} GB free disk space"

RAM_GB=$(awk '/MemTotal/ {printf "%d", $2/1024/1024 + 0.5}' /proc/meminfo)
if [ "$RAM_GB" -lt 15 ]; then warn "${RAM_GB} GB of RAM; 16 GB or more is recommended."; else ok "${RAM_GB} GB of RAM"; fi

for tool in curl tar; do
  command -v "$tool" >/dev/null 2>&1 || fail "'$tool' is not installed." "Install it with your package manager, e.g. sudo apt install $tool"
done

GPU=""
if [ "$FORCE_CPU" = 0 ] && command -v nvidia-smi >/dev/null 2>&1; then
  if Q=$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null | head -n1) && [ -n "$Q" ]; then
    GPU="nvidia"; ok "NVIDIA GPU: $Q"
    VRAM=$(echo "$Q" | awk -F, '{gsub(/[^0-9]/,"",$2); print $2}')
    [ -n "$VRAM" ] && [ "$VRAM" -lt 7500 ] && warn "Under 8 GB of VRAM: the model may not fit; use --cpu if loading fails."
  else
    warn "nvidia-smi exists but cannot talk to the driver. Reboot after a driver install, or fix the driver first."
  fi
fi
if [ -z "$GPU" ] && [ "$FORCE_CPU" = 0 ] && { [ -e /dev/kfd ] || command -v rocminfo >/dev/null 2>&1; }; then
  GPU="amd"
  info "AMD GPU with ROCm detected."
  if [ "$TORCH_BACKEND" = "auto" ]; then
    TORCH_BACKEND="rocm6.4"
    info "Using the ROCm 6.4 PyTorch build (experimental for VoxCPM2). Override with --torch-backend rocm6.3 if needed."
  fi
fi
if [ -z "$GPU" ] && [ "$FORCE_CPU" = 0 ] && command -v lspci >/dev/null 2>&1 && lspci | grep -qiE "VGA.*Intel.*Arc|Display.*Intel.*Arc"; then
  GPU="intel"; info "Intel Arc GPU detected: using the XPU build (experimental)."
  [ "$TORCH_BACKEND" = "auto" ] && TORCH_BACKEND="xpu"
fi
if [ -z "$GPU" ] && [ "$TORCH_BACKEND" = "auto" ]; then
  [ "$FORCE_CPU" = 0 ] && warn "No supported GPU found: installing the CPU build. It works, but speech is slow."
  FORCE_CPU=1
fi
[ "$FORCE_CPU" = 1 ] && TORCH_BACKEND="cpu"
info "PyTorch build: $TORCH_BACKEND"
case "$PYVER" in
  3.10|3.11|3.12|3.13) ;;
  *) warn "Python $PYVER is not supported (some VoxCPM2 dependencies have no ready-made packages for it). Using 3.11."; PYVER="3.11" ;;
esac

# -------------------------------------------------------------------------------------------------
step 2 "Installing uv (Python manager)"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  info "Downloading uv from astral.sh ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh || fail "Could not install uv." "Check your internet connection, or see https://docs.astral.sh/uv/"
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || fail "uv was installed but is not on PATH." "Open a new terminal and run the installer again."
fi
UV_VER="$(uv --version | awk '{print $2}')"
ok "uv $UV_VER"
if [ "$(printf '%s\n' "0.7.0" "$UV_VER" | sort -V | head -n1)" != "0.7.0" ]; then
  info "Updating uv (older versions cannot choose PyTorch builds automatically)..."
  uv self update >/dev/null 2>&1 || warn "Could not update uv; if PyTorch installation fails, update uv yourself."
fi

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
step 4 "Installing PyTorch ($TORCH_BACKEND) and the VoxCPM2 engine (several GB, be patient)"
uv pip install --python "$PY" --torch-backend "$TORCH_BACKEND" torch torchaudio \
  || fail "Installing PyTorch failed." "Check your connection. With an old NVIDIA driver, update it or use --torch-backend cu126 / --cpu."
uv pip install --python "$PY" --torch-backend "$TORCH_BACKEND" -r requirements.txt \
  || fail "Installing the VoxCPM2 engine failed." "Look above for the package that failed."
CHECK=$("$PY" -c "import torch, voxcpm, soundfile; x=hasattr(torch,'xpu') and torch.xpu.is_available(); print(torch.__version__, 'CUDA/ROCm' if torch.cuda.is_available() else ('XPU' if x else 'CPU'))" 2>&1) \
  || fail "The installed packages do not import: $CHECK" "Delete .venv and run the installer again."
ok "PyTorch $CHECK"
if [ "$GPU" = "nvidia" ] && [[ "$CHECK" != *CUDA* ]]; then
  warn "An NVIDIA GPU is present but PyTorch cannot use it. Update the NVIDIA driver and run this installer again."
fi

# -------------------------------------------------------------------------------------------------
step 5 "Writing settings (.env)"
if [ ! -f .env ]; then cp .env.example .env; ok "Created .env from .env.example"; else ok ".env already exists - keeping your settings"; fi
set_env() {  # set_env KEY VALUE  (replaces an existing or commented line, else appends)
  local key="$1" val="$2" tmp
  tmp="$(mktemp)"
  if grep -qE "^[[:space:]]*#?[[:space:]]*$key=" .env; then
    awk -v k="$key" -v v="$val" '$0 ~ "^[[:space:]]*#?[[:space:]]*" k "=" { print k "=" v; next } { print }' .env > "$tmp"
  else
    cat .env > "$tmp"; printf '%s=%s\n' "$key" "$val" >> "$tmp"
  fi
  mv "$tmp" .env
}
[ -n "$PORT" ] && { set_env VOXCPM_PORT "$PORT"; ok "Port set to $PORT"; }
[ -n "$MODEL_DIR" ] && { set_env VOXCPM_MODEL_DIR "$MODEL_DIR"; ok "Model folder set to $MODEL_DIR"; }
[ "$FORCE_CPU" = 1 ] && set_env VOXCPM_DEVICE cpu

# -------------------------------------------------------------------------------------------------
step 6 "Downloading the VoxCPM2 model (~5 GB, one time)"
if [ "$SKIP_MODEL" = 1 ]; then
  info "Skipped (--skip-model). The server downloads it on first start, or run: .venv/bin/python scripts/download_model.py"
else
  "$PY" scripts/download_model.py || warn "The download did not finish. Run the installer again (it resumes) or just start the server."
fi

# -------------------------------------------------------------------------------------------------
step 7 "Final checks"
chmod +x start.sh doctor.sh integrate.sh install-linux.sh 2>/dev/null || true
"$PY" scripts/doctor.py --quick || true

if [ "$SERVICE" = 1 ]; then
  if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    UNIT_DIR="$HOME/.config/systemd/user"; mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/voxcpm2-voice-server.service" <<EOF
[Unit]
Description=VoxCPM2 Voice Server (local text-to-speech)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
Environment=PYTHONUNBUFFERED=1
ExecStart=$PY -m voxcpm_server --no-open
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now voxcpm2-voice-server.service
    ok "systemd user service installed and started (systemctl --user status voxcpm2-voice-server)"
    info "To start it at boot even before you log in: sudo loginctl enable-linger $USER"
  else
    warn "systemd user services are not available here (container/WSL without systemd?). Start with ./start.sh instead."
  fi
fi

if [ "$DESKTOP" = 1 ] || { [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && [ "$YES" = 0 ] && ask "Add VoxCPM2 Voice Server to your application menu?"; }; then
  APPS="$HOME/.local/share/applications"; mkdir -p "$APPS"
  cat > "$APPS/voxcpm2-voice-server.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=VoxCPM2 Voice Server
Comment=Local neural text-to-speech
Exec=$ROOT/start.sh
Path=$ROOT
Terminal=true
Categories=AudioVideo;Audio;Utility;
EOF
  ok "Launcher added to your application menu"
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
