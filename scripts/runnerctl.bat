@echo off
REM runnerctl.bat - runner 백그라운드 제어(start/status/logs/stop).
chcp 65001 > nul
setlocal
set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo ERROR: Python not found at %PY%
    echo Please create .venv and install deps. See docs/08_install.md.
    exit /b 1
)
cd /d "%~dp0.."
"%PY%" -m tools.runnerctl %*
set "RC=%errorlevel%"
exit /b %RC%
