@echo off
REM clean.bat - logs/ + state/ 정리(runner 실행 중이면 거부).
chcp 65001 > nul
setlocal
set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo ERROR: Python not found at %PY%
    echo Please create .venv and install deps. See docs/08_install.md.
    exit /b 1
)
cd /d "%~dp0.."
"%PY%" -m tools.clean %*
set "RC=%errorlevel%"
echo.
echo (exit code: %RC%)
exit /b %RC%
