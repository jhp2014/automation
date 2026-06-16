@echo off
REM zenius-baseline.bat - 현재 EMS 경고 전부를 보고 완료로 흡수(폭풍 진정용 수동 도구)
chcp 65001 > nul
setlocal
set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo ERROR: Python not found at %PY%
    echo Please create .venv and install deps. See docs/08_install.md.
    exit /b 1
)
cd /d "%~dp0.."
"%PY%" -m jobs.zenius --baseline --no-headless %*
set "RC=%errorlevel%"
echo.
echo (exit code: %RC%)
exit /b %RC%
