@echo off
REM jennifer-dry-run.bat - jobs.jennifer --dry-run
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
"%PY%" -m jobs.jennifer --dry-run %*
set "RC=%errorlevel%"
echo.
echo (exit code: %RC%)
pause
exit /b %RC%
