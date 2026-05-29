@echo off
REM server-dry-run.bat - jobs.server --dry-run with hardcoded folders.
REM   target-title is auto-loaded from daily.yaml kworks.target_title.
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
set "IMGBASE=C:\automation\server-helper\images"
"%PY%" -m jobs.server --folder "%IMGBASE%\8 전면" --folder "%IMGBASE%\8 후면" --dry-run %*
set "RC=%errorlevel%"
echo.
echo (exit code: %RC%)
pause
exit /b %RC%
