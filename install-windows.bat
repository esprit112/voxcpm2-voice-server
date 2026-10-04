@echo off
rem Double-click to install VoxCPM2 Voice Server. Options are passed on, e.g.:
rem   install-windows.bat -Cpu        install-windows.bat -SkipModel        install-windows.bat -Yes
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1" %*
echo.
pause
