@echo off
REM capture-dry-run.bat - jobs.capture --dry-run (capture + safety_check only)
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
"%PY%" -m jobs.capture --dry-run %*
set "RC=%errorlevel%"
echo.
echo (exit code: %RC%)
pause
exit /b %RC%
