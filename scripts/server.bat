@echo off
REM server.bat - jobs.server with folder names under jobs/server/images.
REM   target-title is auto-loaded from daily.yaml kworks.target_title.
REM   Folders are relative names resolved to jobs/server/images/<name>.
chcp 65001 > nul
setlocal
set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo ERROR: Python not found at %PY%
    echo Please create .venv and install deps. See docs/08_install.md.
    pause
    exit /b 1
)
cd /d "%~dp0.."
"%PY%" -m jobs.server --folder "8 전면" --folder "8 후면" --no-headless --no-submit %*
set "RC=%errorlevel%"
echo.
echo (exit code: %RC%)
pause
exit /b %RC%
