# Troubleshooting

**Always start with the doctor:** `doctor-windows.bat` (Windows) or `./doctor.sh` (Linux/macOS).
It checks every part of the install and prints a fix for each problem it finds. Installers write a full
log to `install.log` in this folder.

---

## Installing

**"Windows protected your PC" / SmartScreen**
Click **More info → Run anyway**. The scripts are plain text, so you can open them in Notepad to see exactly
what they do.

**"running scripts is disabled on this system" (PowerShell)**
Use `install-windows.bat`, which bypasses the policy for this one script only. Or run
`powershell -ExecutionPolicy Bypass -File install-windows.ps1`.

**macOS: "cannot be opened because it is from an unidentified developer" / "Operation not permitted"**
In Terminal, inside the folder: `xattr -dr com.apple.quarantine .` then `bash install-macos.sh`.

**"Permission denied" running `./install-linux.sh` or `./start.sh`**
ZIP downloads lose the executable bit. Run `bash install-linux.sh` once; it restores the bits for the
other scripts. Or run `chmod +x *.sh`.

**`/usr/bin/env: 'bash\r': No such file or directory`**
The scripts got Windows line endings, usually from copying them through Windows. Fix with
`sed -i 's/\r$//' *.sh`, or re-download. A git clone gets this right automatically (`.gitattributes`).

**"Failed building wheel for kaldifst" / "'cmake' is not recognized" (Windows)**
You installed into your own Python 3.14+ with `pip install voxcpm ...`. A VoxCPM2 dependency (`kaldifst`) has no
ready-made 64-bit Windows package for Python 3.14, so pip tries to compile it and fails. Nothing was installed.
Use `install-windows.bat` instead: it creates a private Python 3.11 environment and installs the correct
**CUDA** build of PyTorch. A plain `pip install torch` on Windows gives a CPU-only build, so your GPU would sit idle.

**The download of PyTorch or the model is very slow or fails**
- Run the installer again: everything resumes where it stopped.
- Behind a proxy: set `HTTPS_PROXY=http://proxy:port` before installing.
- Hugging Face blocked or slow in your region: add `HF_ENDPOINT=https://hf-mirror.com` to `.env` and run
  `python scripts/download_model.py` (with the `.venv` Python).

**Not enough disk space**
You need about 15 GB. Move the folder to a bigger drive, or keep the model elsewhere with
`-ModelDir` / `--model-dir`.

**Path problems on Windows (strange errors, files "not found")**
Move the folder to a short path with only English letters, e.g. `C:\VoxCPM2`. Avoid OneDrive-synced
folders: OneDrive can lock or offload the 4 GB model file.

---

## GPU

**The doctor says "CUDA not available" but I have an NVIDIA card**
1. Update the driver from <https://www.nvidia.com/drivers> (R550 or newer is recommended), then reboot.
2. Run the installer again. It detects the driver and installs a matching PyTorch build.
3. Old driver you cannot update: `install-windows.bat -TorchBackend cu118`, or `--torch-backend cu118` on Linux.

**"CUDA out of memory"**
- Close other GPU programs (games, Stable Diffusion, ComfyUI, browsers with hardware acceleration).
- Lower `VOXCPM_MAX_CHARS_PER_PIECE` to 200 in `.env`.
- Cards with 6 GB or less: use `VOXCPM_DEVICE=cpu`.

**AMD GPU on Linux**
ROCm support is experimental. Your GPU must be supported by ROCm, and your user must be in the `render` and
`video` groups (`sudo usermod -aG render,video $USER`, then log out and in). Try
`--torch-backend rocm6.3` if 6.4 does not work. AMD on Windows is not supported by PyTorch; use the CPU build
or WSL2.

**Apple Silicon is slow or falls back to the CPU**
Update macOS (13+ is needed for MPS) and keep `PYTORCH_ENABLE_MPS_FALLBACK=1` in `.env`. With 8 GB of
memory, close other apps while generating.

**Make it faster on NVIDIA**
Set `VOXCPM_OPTIMIZE=1` in `.env` to use torch.compile: the first start is slower, every generation after
that is faster. Linux works out of the box; on Windows also run `.venv\Scripts\python -m pip install triton-windows`.
If compiling fails, the server falls back automatically. You can also set `VOXCPM_STEPS=8` for a small speed
gain at a small quality cost.

---

## Running

**"Port 8808 is used by another program"**
Start on another port with `start-windows.bat --port 8809` or `./start.sh --port 8809`, or set `VOXCPM_PORT`
in `.env`. Then re-run the agent integrations so they use the new port.

**"already running" and nothing happens**
A server is already up: open <http://127.0.0.1:8808>. To restart it, close its window, or run
`systemctl --user restart voxcpm2-voice-server` / `launchctl kickstart -k gui/$(id -u)/io.github.voxcpm2.voiceserver`.

**The first request takes a long time**
The model loads once per start, which takes 1–2 minutes (longer on CPU and on the very first run). Later
requests are fast. `/health` and the Engine page show the model state.

**The web page cannot reach the server from another device**
By default the server only listens on this computer. Set `VOXCPM_HOST=0.0.0.0` and a `VOXCPM_API_KEY` in
`.env`, restart, and allow the port through the firewall. On Windows, accept the firewall prompt or run
`netsh advfirewall firewall add rule name="VoxCPM2" dir=in action=allow protocol=TCP localport=8808` as admin.

**AAC fails ("needs FFmpeg")**
Install FFmpeg (`winget install Gyan.FFmpeg`, `sudo apt install ffmpeg`, `brew install ffmpeg`) or use MP3/Opus.

---

## Voices

**A cloned voice sounds wrong or noisy**
Use 5–30 seconds of one person speaking clearly, with no music, no echo and no other voices. Type the exact
transcript when cloning; it noticeably improves the likeness.

**A preset voice changed or sounds different**
Presets speak from `voices/presets/<id>.wav`. If that file is missing, the server designs a new one, which
sounds different. Restore the original files from the repository, or use **Voices → Restore presets**.

**Emotion markers are read out loud**
Markers must use square brackets, e.g. `[happy]`, not `(happy)` or `*happy*`.

---

## AI agent integrations

**The agent does not show the voice tools**
Restart the agent completely after running the integration. Check that it is registered: `claude mcp list`,
`codex mcp list`, `/mcp` in Gemini CLI and Claude Code. Run the integration again with `--dry-run` to see
what it writes.

**"The VoxCPM2 Voice Server is not running"**
The MCP tools start the server automatically for local addresses. If that fails, start it yourself and look
at `data/server.log`.

**Hermes says the provider is unknown**
Restart Hermes after installing, and check that the plugin is enabled with `hermes plugins list`. You can use
`./integrate.sh hermes --mode openai` instead, which needs no plugin.

**Open WebUI in Docker cannot connect**
Inside Docker, `127.0.0.1` is the container itself. Use `http://host.docker.internal:8808/v1`, and set
`VOXCPM_HOST=0.0.0.0` (and an API key) for the voice server.

**I want to undo everything an integration changed**
Run `./integrate.sh <runtime> --uninstall`. Every changed config file also has a `*.bak-<date>` copy next to it.

---

## Reporting a bug

Open an issue with:
1. the output of `doctor-windows.bat --json` or `./doctor.sh --json`
2. `install.log` and/or `data/server.log` (remove anything private)
3. what you did, what you expected, and what happened
