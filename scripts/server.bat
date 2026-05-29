@echo off
REM server.bat - jobs.server with hardcoded folder.
REM   target-title is read from .env (KWORKS_TARGET_TITLE).
REM   Folder is hardcoded as "8 jeonmyeon" (8 전면). Edit the path below if needed.
chcp 65001 > nul
setlocal
pushd "%~dp0.."
".venv\Scripts\python.exe" -m jobs.server --folder "C:\automation\server-helper\images\8 전면" %*
set "RC=%errorlevel%"
popd
echo.
echo (exit code: %RC%)
pause
exit /b %RC%
