@echo off
rem VoxCPM2 Voice Server - start (Windows). Double-click, or run with options:
rem   start-windows.bat --no-open        do not open the browser
rem   start-windows.bat --device cpu     force CPU
rem   start-windows.bat --port 8809      another port
setlocal
cd /d "%~dp0"
title VoxCPM2 Voice Server
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  VoxCPM2 Voice Server is not installed yet.
    echo  Double-click install-windows.bat first.
    echo.
    pause
    exit /b 1
)
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
".venv\Scripts\python.exe" -m voxcpm_server --open %*
set CODE=%ERRORLEVEL%
if not "%CODE%"=="0" (
    echo.
    echo  The server stopped with an error ^(code %CODE%^).
    echo  Run doctor-windows.bat for a diagnosis, or see TROUBLESHOOTING.md.
    pause
)
exit /b %CODE%
