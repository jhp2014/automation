@echo off
REM capture-dry-run.bat - jobs.capture --dry-run (capture + safety_check only)
chcp 65001 > nul
setlocal
pushd "%~dp0.."
".venv\Scripts\python.exe" -m jobs.capture --dry-run %*
set "RC=%errorlevel%"
popd
echo.
echo (exit code: %RC%)
pause
exit /b %RC%
