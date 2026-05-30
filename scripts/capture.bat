@echo off
REM capture.bat - jobs.capture
REM   target-title is auto-loaded from daily.yaml kworks.target_title.
chcp 65001 > nul
setlocal
set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo ERROR: Python not found at %PY%
    echo Please create .venv and install deps. See docs/08_install.md.
    exit /b 1
)
cd /d "%~dp0.."
"%PY%" -m jobs.capture --no-headless --no-submit %*
set "RC=%errorlevel%"
echo.
echo (exit code: %RC%)
exit /b %RC%
