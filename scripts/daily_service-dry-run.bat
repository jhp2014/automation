@echo off
REM daily_service-dry-run.bat - jobs.daily_service --dry-run
chcp 65001 > nul
setlocal
pushd "%~dp0.."
".venv\Scripts\python.exe" -m jobs.daily_service --dry-run %*
set "RC=%errorlevel%"
popd
echo.
echo (exit code: %RC%)
pause
exit /b %RC%
