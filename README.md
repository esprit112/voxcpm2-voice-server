# VoxCPM2 Voice Server

**Private, studio-quality text-to-speech that runs on your own computer.** A web app for making voices, an
OpenAI-compatible API for your tools, and one-command hookups that give your AI agents (Hermes, OpenClaw,
Claude Code, Codex, Gemini CLI, OpenCode, Cursor, Open WebUI, …) a real voice.

Powered by [VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) from OpenBMB: 48 kHz audio, 30 languages,
voice design from a text description, and voice cloning from a few seconds of audio.

- 🎙️ **8 ready-made voices**, plus voices you **design** ("a warm elderly Scottish storyteller") or **clone**
- 🎭 **Inline emotion markers**: `[excited] We won! [pause:500ms] [whisper] Don't tell anyone.`
- 🔌 **Drop-in OpenAI API**: `POST /v1/audio/speech` works with any OpenAI TTS client
- 🖥️ **Windows 11, Linux and macOS (Apple Silicon)**, on NVIDIA, AMD (ROCm), Intel Arc, Apple GPUs, or CPU
- 🔒 **Offline and private**: after the one-time model download nothing leaves your machine
- 🧰 One-click installers, a `doctor` that explains problems in plain English, Docker, a systemd service, and launchd support

---

## Contents

1. [Requirements](#requirements)
2. [Install](#install): [Windows](#windows-10--11) · [Linux](#linux) · [macOS](#macos-apple-silicon) · [Docker](#docker)
3. [Using the web app](#using-the-web-app)
4. [Connect your AI agents](#connect-your-ai-agents)
5. [Emotion markers](#emotion-markers)
6. [API](#api)
7. [Settings](#settings)
8. [Updating & uninstalling](#updating--uninstalling)
9. [Troubleshooting](#troubleshooting)
10. [Project layout](#project-layout) · [Licence & responsible use](#licence--responsible-use)

---

## Requirements

| | Minimum | Recommended |
|---|---|---|
| **OS** | Windows 10 1809+, Ubuntu 20.04+ / any modern Linux, macOS 13+ (Apple Silicon) | Windows 11, Ubuntu 22.04+, macOS 14+ |
| **GPU** | none (CPU works, slowly) | NVIDIA with **8 GB+ VRAM** (RTX 3060 or better), Apple M-series with 16 GB+ |
| **RAM** | 8 GB | 16 GB+ |
| **Disk** | 15 GB free (PyTorch ~4 GB, model ~5 GB) | SSD |
| **Internet** | for installation and the one-time model download | |

You **don't** need to install Python, CUDA, Git or FFmpeg first. The installer fetches a private Python
(through [uv](https://docs.astral.sh/uv/)) and the right PyTorch build for your hardware. It keeps everything
inside this folder and needs no admin rights.

**How fast is it?** On an RTX 3060, one second of speech takes about 1.7 seconds to generate (RTF ≈ 1.7).
The first start loads the model, which takes about 1–2 minutes. Apple Silicon is slower and CPU-only is much
slower, but both work.

> Intel Macs are not supported: PyTorch no longer ships Intel macOS builds. Run the server on another
> computer and connect to it over your network instead.

---

## Install

First get the code: either **Code → Download ZIP** on GitHub and unzip it, or
`git clone https://github.com/esprit112/voxcpm2-voice-server.git`.
Put it somewhere with a short path and no special characters, e.g. `C:\VoxCPM2` or `~/voxcpm2-voice-server`.

### Windows 10 / 11

1. Open the folder and **double-click `install-windows.bat`**.
   *If SmartScreen warns you about an unrecognised app, click **More info → Run anyway**.*
2. Wait. The installer shows each step: it checks your PC, installs uv + Python 3.11, installs PyTorch for
   your GPU, downloads the model (~5 GB), and runs a health check. It then offers a desktop shortcut.
3. **Double-click `start-windows.bat`.** Your browser opens at **http://127.0.0.1:8808**.

Options (run from PowerShell, or pass them to the `.bat`):

```powershell
.\install-windows.bat -Cpu                      # no NVIDIA GPU / force the CPU build
.\install-windows.bat -TorchBackend cu126       # choose the CUDA build yourself (older drivers)
.\install-windows.bat -SkipModel                # download the model later (the server fetches it on first start)
.\install-windows.bat -ModelDir D:\models\voxcpm2  # use / keep the model elsewhere
.\install-windows.bat -Port 8809 -DesktopShortcut -Autostart -Yes
```

`-Autostart` starts the server minimised whenever you log in. To undo it, delete the shortcut from `shell:startup`.

### Linux

```bash
cd voxcpm2-voice-server
bash install-linux.sh            # add --help to see every option
./start.sh                       # then open http://127.0.0.1:8808
```

The installer detects **NVIDIA** (CUDA), **AMD** (ROCm, experimental), **Intel Arc** (XPU, experimental), and
falls back to the CPU otherwise. It also detects WSL2 (NVIDIA works there with the Windows driver). Useful options:

```bash
bash install-linux.sh --service        # run as a systemd user service (starts at login; see output for boot-time)
bash install-linux.sh --desktop        # add an app-menu launcher
bash install-linux.sh --cpu            # force the CPU build
bash install-linux.sh --torch-backend rocm6.3   # pick the PyTorch build yourself
```

Service control: `systemctl --user status|restart|stop voxcpm2-voice-server` and
`journalctl --user -u voxcpm2-voice-server -f`.

### macOS (Apple Silicon)

```bash
cd voxcpm2-voice-server
bash install-macos.sh            # --service to start automatically at login
./start.sh
```

It uses the Apple GPU (Metal/MPS) automatically. If macOS blocks the scripts because they were downloaded
from the internet, the installer removes that quarantine flag for this folder. If even the installer is
blocked, run `xattr -dr com.apple.quarantine .` in the folder first.

### Docker

For NVIDIA GPUs (needs the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)):

```bash
docker compose up -d            # first start downloads the ~5 GB model into a volume
docker compose logs -f          # wait for "Ready on ..."
```

CPU only: remove the `deploy:` block in `docker-compose.yml` and set `TORCH_BACKEND: cpu`.
Other options: `docker build --build-arg TORCH_BACKEND=rocm6.4 .` for AMD.

### Already have the model?

Point the installer at it (`-ModelDir` / `--model-dir`), or set `VOXCPM_MODEL_DIR` in `.env`. The folder
must contain `config.json`, `model.safetensors`, `audiovae.pth` and `tokenizer.json`. Check it with
`python scripts/download_model.py --check --dir <folder>`.

---

## Using the web app

Open **http://127.0.0.1:8808** while the server runs.

- **Speak**: type or paste text, pick a voice, an emotion, the speed and the pitch, then press **Generate**
  (Ctrl+Enter). Play it, download it as MP3/WAV/FLAC/Opus, or find it again in **History**.
- **Stream**: hear long texts while they are still being generated.
- **Design a voice**: describe a voice in words; the model invents it and saves it to your library.
- **Clone a voice**: upload or record 5–30 seconds of clean speech, and optionally type exactly what is said
  (this improves accuracy). **Only clone voices you have permission to use.**
- **Voices**: rename, describe, delete, or restore the presets.
- **Engine**: see the GPU/CPU in use, the model state, and the speed statistics.

---

## Connect your AI agents

**Double-click `integrate-windows.bat`** on Windows, or run **`./integrate.sh`** on Linux/macOS. A menu shows
which runtimes it found; pick the ones you want. You can also name them directly:

```bash
./integrate.sh claude-code codex          # specific runtimes
./integrate.sh all --yes                  # everything found on this computer
./integrate.sh --list                     # what is detected
./integrate.sh hermes --uninstall         # undo
./integrate.sh opencode --dry-run         # show the change without making it
```

| Runtime | How it connects | What you get |
|---|---|---|
| **Hermes Agent** | native TTS provider plugin (or `--mode openai`) | Hermes speaks with your voices; voice picker in Settings; Opus voice notes for Telegram/Discord |
| **OpenClaw** | OpenAI-compatible `messages.tts` provider | spoken replies and voice notes in every channel; `/tts always` |
| **Claude Code** | MCP server + optional **Stop hook** (`--speak-replies`) | `speak` / `text_to_speech_file` tools; optionally reads every reply aloud |
| **OpenAI Codex CLI** | MCP server in `~/.codex/config.toml` | voice tools in Codex CLI, IDE extension and app |
| **Gemini CLI** | MCP server in `~/.gemini/settings.json` | voice tools |
| **OpenCode** | local MCP server in `opencode.json` | voice tools |
| **Claude Desktop, Cursor, Windsurf, VS Code, LM Studio** | MCP server in each app's config | voice tools |
| **Open WebUI** | built-in "OpenAI" TTS engine | the speaker button / auto-playback reads replies with your voices |
| **Anything else** | OpenAI API at `http://127.0.0.1:8808/v1` | use model `voxcpm2` and any voice id |

Every installer **backs up** the file it changes (`*.bak-<date>`), can be **run again safely**, supports
`--uninstall` and `--dry-run`, and accepts `--voice <id or name>`, `--server-url` and `--api-key`.

**The MCP tools** (`speak`, `text_to_speech_file`, `list_voices`, `design_voice`, `voice_server_status`)
**start the voice server automatically** if it isn't running. Just ask your agent *"read that summary out loud"*.

Details and manual set-up for each runtime: **[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)**.

---

## Emotion markers

Put markers in square brackets anywhere in the text. VoxCPM2 takes one style per generation, so the server
splits the text at markers and joins the pieces seamlessly.

| Marker | Effect |
|---|---|
| `[happy]` `[cheerful]` `[excited]` `[calm]` `[sad]` `[tired]` `[angry]` `[frustrated]` `[concerned]` `[fearful]` `[surprised]` `[empathetic]` `[authoritative]` `[dramatic]` `[whisper]` | delivery from here until the next style marker |
| `[neutral]` | plain delivery |
| `[normal]` `[reset]` `[/happy]` | back to the request's overall emotion |
| `[pause]` `[pause:500ms]` `[pause:1.5s]` `[breath]` | silence |
| `[anything else]` e.g. `[like a pirate captain]` | free-form style instruction |

```text
[calm] Welcome back. [pause:600ms] [excited] Today we finally ship version two! [normal] Let's begin.
```

---

## API

The base URL is `http://127.0.0.1:8808`. If you set `VOXCPM_API_KEY`, send `Authorization: Bearer <key>`.

### OpenAI-compatible speech

```bash
curl http://127.0.0.1:8808/v1/audio/speech -H "Content-Type: application/json" \
  -d '{"model":"voxcpm2","voice":"vox-solas-british","input":"Hello! [happy] Nice to meet you.","response_format":"mp3"}' \
  -o hello.mp3
```

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8808/v1", api_key="not-needed")
client.audio.speech.create(model="voxcpm2", voice="vox-aura-studio", input="Hi there!").write_to_file("hi.mp3")
```

| Field | Values |
|---|---|
| `input` | text, up to 20,000 characters (markers allowed) |
| `voice` | voice id **or name** (`"Aura Studio"`); omitted = default voice |
| `response_format` | `mp3` (default), `wav`, `flac`, `opus`, `aac`*, `pcm` (16-bit mono 48 kHz) |
| `speed` | 0.25–4.0 (pitch is preserved; 0.75–1.5 sounds most natural) |
| extras | `emotion`, `style` (free text), `pitch` (semitones, −12…12), `cfg_value`, `inference_timesteps` |

\* AAC needs FFmpeg installed. The other formats are encoded natively.

### All endpoints

| Method & path | Purpose |
|---|---|
| `POST /v1/audio/speech` | speech (OpenAI-compatible) |
| `GET /v1/audio/voices` (alias `/v1/voices`) | voices in OpenAI-style form |
| `GET /v1/models` | lists `voxcpm2` |
| `GET /health` | status, device, model state, statistics |
| `GET /api/voices` | full voice library |
| `POST /api/voices/design` | `{"name","description"}` → new designed voice |
| `POST /api/voices/clone` | `{"name","audio_base64","transcript"?}` → new cloned voice |
| `PATCH /api/voices/<id>` / `DELETE /api/voices/<id>` | edit / delete a voice |
| `POST /api/voices/restore` | restore the preset voices |
| `GET /api/voices/<id>/reference.wav` | the voice's reference recording |
| `POST /api/stream` | same body as `/v1/audio/speech`; streams raw float32 mono PCM while generating |
| `POST /api/device` | `{"device":"cuda"/"cpu"/…}` switch device (reloads the model) |

---

## Settings

Edit **`.env`** in this folder (the installer creates it from [`.env.example`](.env.example), which explains
every option), then restart the server. Command-line flags override `.env`: run `python -m voxcpm_server --help`.

The most useful ones:

| Setting | Default | |
|---|---|---|
| `VOXCPM_PORT` | `8808` | |
| `VOXCPM_HOST` | `127.0.0.1` | `0.0.0.0` to allow other devices on your network (**set an API key**) |
| `VOXCPM_API_KEY` | empty | require a key for API calls |
| `VOXCPM_DEVICE` | `auto` | `cuda`, `cuda:1`, `mps`, `cpu` |
| `VOXCPM_DEFAULT_VOICE` | `vox-aura-studio` | |
| `VOXCPM_STEPS` | `10` | quality vs speed (4–50) |
| `VOXCPM_OPTIMIZE` | `0` | `1` = torch.compile on NVIDIA (slower start, faster speech) |
| `HF_ENDPOINT` | | Hugging Face mirror if the download is blocked or slow |

Your own voices live in **`data/voices/`**. Back up that folder to keep them.

---

## Updating & uninstalling

**Update:** download the new version over the old folder (or `git pull`), then run the installer again. It
keeps `.env`, `data/` and the model, and only updates what changed.

**Uninstall:**
1. Remove agent hookups: `integrate-windows.bat all --uninstall` / `./integrate.sh all --uninstall`
2. Remove autostart if you enabled it: Windows `shell:startup` shortcut ·
   Linux `systemctl --user disable --now voxcpm2-voice-server` ·
   macOS `launchctl bootout gui/$(id -u)/io.github.voxcpm2.voiceserver`
3. Delete the folder. Nothing was installed anywhere else, except `uv` in `~/.local/bin` and its cache, which
   you can remove with `uv cache clean`.

---

## Troubleshooting

**Run the doctor first:** `doctor-windows.bat` or `./doctor.sh`. It checks Python, PyTorch, the GPU, the model
files, the port and the server, and prints a fix for anything wrong.

The most common problems and fixes are in **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)**. Still stuck? Open an
issue and include the output of `doctor --json` plus `install.log`.

To check that a running server works end to end: `.venv/bin/python scripts/smoke_test.py --full`
(on Windows, `.venv\Scripts\python scripts\smoke_test.py --full`).

---

## Project layout

```
install-windows.bat / .ps1   install-linux.sh   install-macos.sh     installers
start-windows.bat            start.sh                                start the server (+ open the browser)
doctor-windows.bat           doctor.sh                               diagnose problems
integrate-windows.bat        integrate.sh                            connect AI agent runtimes
voxcpm_server/               the server (pure Python, standard-library HTTP)
web/index.html               the web app (one file, works offline)
voices/presets/              reference recordings of the 8 preset voices
integrations/                MCP server + one installer per runtime
scripts/                     download_model.py, doctor.py, smoke_test.py
tests/                       fast tests (no GPU needed), run in CI on all three OSes
Dockerfile, docker-compose.yml
```

Development: `python -m unittest discover -s tests -v` runs in under a second without PyTorch.

## Licence & responsible use

The code is under the [MIT](LICENSE) licence. The VoxCPM2 model and the `voxcpm` package are © OpenBMB,
under Apache-2.0. The preset voices were *designed* by the model from text descriptions; they are not
recordings of real people.

**Voice cloning is powerful. Only clone your own voice, or voices you have explicit permission to use. Never
use this software to impersonate people, deceive, or defraud.** Label AI-generated audio where it could be
mistaken for a real person.
