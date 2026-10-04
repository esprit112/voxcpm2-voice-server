@echo off
rem Give your AI agents a voice (Hermes, OpenClaw, Claude Code, Codex, Gemini CLI, OpenCode, Cursor, ...).
rem Double-click for a menu, or e.g.:  integrate-windows.bat claude-code --speak-replies
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo The server is not installed yet. Run install-windows.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" integrations\install.py %*
echo.
pause
