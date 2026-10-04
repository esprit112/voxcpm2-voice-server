# Connecting AI agent runtimes

The easy way is `integrate-windows.bat` on Windows or `./integrate.sh` on Linux/macOS (see the README). This
page explains what each installer changes, so you can set things up by hand, on another machine, or in a
container.

There are two ways an agent can use the server:

- **OpenAI-compatible TTS.** The runtime already has a "text-to-speech provider" setting. Point it at
  `http://127.0.0.1:8808/v1` with model `voxcpm2` and a voice id. Used by OpenClaw, Open WebUI, Hermes
  (`--mode openai`) and any OpenAI SDK.
- **MCP tools.** The agent gets tools it can call: `speak`, `text_to_speech_file`, `list_voices`,
  `design_voice` and `voice_server_status`. The MCP server is `integrations/mcp/voxcpm_mcp.py`, uses only the
  standard library, talks over stdio, and starts the voice server automatically when needed. Used by Claude
  Code, Codex, Gemini CLI, OpenCode, Claude Desktop, Cursor, Windsurf, VS Code and LM Studio.

In the examples below, `PY` is this folder's Python (`<folder>/.venv/Scripts/python.exe` on Windows,
`<folder>/.venv/bin/python` elsewhere) and `MCP` is `<folder>/integrations/mcp/voxcpm_mcp.py`. Any
Python 3.8+ can run the MCP server.

MCP server environment variables:

| Variable | Default | |
|---|---|---|
| `VOXCPM_URL` | `http://127.0.0.1:8808` | server address |
| `VOXCPM_API_KEY` | | if the server requires one |
| `VOXCPM_VOICE` | server default | this agent's voice (id or name) |
| `VOXCPM_OUTPUT_DIR` | `<folder>/outputs` | where `text_to_speech_file` saves audio |
| `VOXCPM_AUTOSTART` | `1` | start the local server if it is not running |

Audio playback for `speak`: Windows uses the built-in Media.SoundPlayer, macOS uses `afplay`, and Linux uses
the first of `pw-play`, `paplay`, `aplay`, `ffplay` or `mpv` that it finds.

---

## Hermes Agent

`./integrate.sh hermes` installs a **native TTS provider plugin** into `$HERMES_HOME/plugins/voxcpm2_server`.
The plugin is the `integrations/hermes/plugin/` folder, with the voice list, server URL and folder filled in.
The installer then runs `hermes plugins enable voxcpm2_server` and `hermes config set tts.provider voxcpm2_server`.

You get a voice picker in the plugin settings (gear icon), automatic server start-up, and Opus voice notes on
Telegram/Discord (setting "Voice messages"). Re-run the installer after you create new voices to refresh the
picker. Use `--keep-provider` to install without switching provider.

**No plugin (`--mode openai`):**
```bash
hermes config set tts.provider openai
hermes config set tts.openai.base_url http://127.0.0.1:8808/v1
hermes config set tts.openai.model voxcpm2
hermes config set tts.openai.voice vox-aura-studio
hermes config set tts.openai.api_key not-needed
```
Hermes may warn that `base_url`/`api_key` are unknown keys. They are saved and work, so the warning is harmless.

## OpenClaw

OpenClaw treats non-default OpenAI settings as an OpenAI-compatible TTS endpoint. The installer uses
`openclaw config set` when the CLI exists, and otherwise edits `~/.openclaw/openclaw.json` (with a backup):

```json5
{
  messages: {
    tts: {
      provider: "openai",
      providers: {
        openai: { baseUrl: "http://127.0.0.1:8808/v1", apiKey: "local-voxcpm2", model: "voxcpm2", voice: "vox-aura-studio" }
      },
      // auto: "always"   // speak every reply (or use /tts always in chat)
    }
  }
}
```
Older OpenClaw versions use `messages.tts.openai` instead of `messages.tts.providers.openai`. The installer
tries both, and `--legacy-schema` forces the old one. Run `openclaw restart` afterwards.
Options: `--auto always|off|inbound|tagged`.

## Claude Code

```bash
claude mcp add --scope user -e VOXCPM_URL=http://127.0.0.1:8808 voxcpm2 -- PY MCP
```

**Read every reply aloud (`--speak-replies`)** adds a `Stop` hook to `~/.claude/settings.json`:
```json
{ "hooks": { "Stop": [ { "hooks": [ { "type": "command", "command": "\"PY\" \"<folder>/integrations/claude_code/speak_hook.py\"", "timeout": 10 } ] } ] } }
```
The hook works like this:
- It reads `last_assistant_message`, removes markdown, code blocks and links, and shortens long replies at a
  sentence boundary (`VOXCPM_HOOK_MAX_CHARS`, default 600).
- It speaks in the background, so it never slows Claude down, and cuts off the previous reply when a new one starts.
- Its settings live in `integrations/claude_code/hook_config.json`.

Toggle it in Claude Code with `/hooks`, or remove it with `--uninstall`.

## OpenAI Codex CLI

The installer appends this to `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`):
```toml
[mcp_servers.voxcpm2]
command = 'PY'
args = ['MCP']
env = { VOXCPM_URL = 'http://127.0.0.1:8808' }
startup_timeout_sec = 20
tool_timeout_sec = 900
```
The long tool timeout matters: the first speech after a cold start includes model loading. Check it with
`codex mcp list`.

## Gemini CLI

The installer adds this to `~/.gemini/settings.json`:
```json
{ "mcpServers": { "voxcpm2": { "command": "PY", "args": ["MCP"], "env": { "VOXCPM_URL": "http://127.0.0.1:8808" }, "timeout": 900000, "trust": false } } }
```
`--trust` lets Gemini call the voice tools without asking each time.

## OpenCode

The installer adds this to `~/.config/opencode/opencode.json` (or `opencode.jsonc`):
```json
{ "$schema": "https://opencode.ai/config.json",
  "mcp": { "voxcpm2": { "type": "local", "command": ["PY", "MCP"], "environment": { "VOXCPM_URL": "http://127.0.0.1:8808" }, "enabled": true } } }
```

## Claude Desktop, Cursor, Windsurf, VS Code, LM Studio

`./integrate.sh mcp-clients` updates every one of these apps that it finds installed (or use `--client NAME`):

| App | File | Key |
|---|---|---|
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` · `~/Library/Application Support/Claude/…` · `~/.config/Claude/…` | `mcpServers` |
| Cursor | `~/.cursor/mcp.json` | `mcpServers` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` |
| VS Code (Copilot agent mode) | `<user settings dir>/mcp.json` | `servers` (with `"type": "stdio"`) |
| LM Studio | `~/.lmstudio/mcp.json` | `mcpServers` |

Each entry is: `"voxcpm2": { "command": "PY", "args": ["MCP"], "env": { "VOXCPM_URL": "http://127.0.0.1:8808" } }`.

## Open WebUI

These go in Admin Panel → Settings → Audio → Text-to-Speech, or in environment variables on the first
container start:
```
AUDIO_TTS_ENGINE=openai
AUDIO_TTS_OPENAI_API_BASE_URL=http://host.docker.internal:8808/v1
AUDIO_TTS_OPENAI_API_KEY=not-needed
AUDIO_TTS_MODEL=voxcpm2
AUDIO_TTS_VOICE=vox-aura-studio
```
Docker needs `--add-host=host.docker.internal:host-gateway`, and the voice server must listen on
`VOXCPM_HOST=0.0.0.0`; set a `VOXCPM_API_KEY` too. Open WebUI keeps settings in its database, so the env vars
only apply to a fresh install. Change an existing one in the Admin panel.

## Anything else (OpenAI SDKs, n8n, Home Assistant, SillyTavern, …)

Use an OpenAI TTS provider with base URL `http://127.0.0.1:8808/v1`, model `voxcpm2`, any API key (unless
you set one), and a voice id from `GET /v1/audio/voices`.

## Agent on another machine

Run the server with `VOXCPM_HOST=0.0.0.0` and a `VOXCPM_API_KEY`. Then pass
`--server-url http://<server-ip>:8808 --api-key <key>` to the integration installer. The MCP server does not
auto-start a remote server, and `speak` plays audio on the machine where the **agent** runs.
