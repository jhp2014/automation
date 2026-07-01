@echo off
REM gen-daily.bat - daily.base.yaml + 실행 시각 -> config/daily.yaml 생성.
chcp 65001 > nul
setlocal
set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo ERROR: Python not found at %PY%
    echo Please create .venv and install deps. See docs/08_install.md.
    exit /b 1
)
cd /d "%~dp0.."
"%PY%" -m tools.gen_daily %*
set "RC=%errorlevel%"
echo.
echo (exit code: %RC%)
exit /b %RC%
