@echo off
REM dry-run-all.bat - run zenius / daily_service / jennifer dry-run sequentially.
REM   Continues even if one fails; shows per-job exit code summary at the end.
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

echo ============================================================
echo [1/3] zenius --dry-run
echo ============================================================
"%PY%" -m jobs.zenius --dry-run
set "RC1=%errorlevel%"

echo.
echo ============================================================
echo [2/3] daily_service --dry-run
echo ============================================================
"%PY%" -m jobs.daily_service --dry-run
set "RC2=%errorlevel%"

echo.
echo ============================================================
echo [3/3] jennifer --dry-run
echo ============================================================
"%PY%" -m jobs.jennifer --dry-run
set "RC3=%errorlevel%"

echo.
echo ============================================================
echo Summary  zenius=%RC1%  daily_service=%RC2%  jennifer=%RC3%
echo ============================================================
pause
exit /b 0
