@echo off
REM capture.bat - jobs.capture (target-title from .env KWORKS_TARGET_TITLE)
chcp 65001 > nul
setlocal
pushd "%~dp0.."
".venv\Scripts\python.exe" -m jobs.capture %*
set "RC=%errorlevel%"
popd
echo.
echo (exit code: %RC%)
pause
exit /b %RC%
