@echo off
rem Diagnose the VoxCPM2 Voice Server install and print fixes.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\doctor.py %*
) else (
    echo Not installed yet - double-click install-windows.bat first.
)
pause
