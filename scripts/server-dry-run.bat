@echo off
REM server-dry-run.bat - jobs.server --dry-run with hardcoded folder.
chcp 65001 > nul
setlocal
pushd "%~dp0.."
".venv\Scripts\python.exe" -m jobs.server --folder "C:\automation\server-helper\images\8 전면" --dry-run %*
set "RC=%errorlevel%"
popd
echo.
echo (exit code: %RC%)
pause
exit /b %RC%
