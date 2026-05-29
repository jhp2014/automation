@echo off
REM jennifer-dry-run.bat - jobs.jennifer --dry-run
chcp 65001 > nul
setlocal
pushd "%~dp0.."
".venv\Scripts\python.exe" -m jobs.jennifer --dry-run %*
set "RC=%errorlevel%"
popd
echo.
echo (exit code: %RC%)
pause
exit /b %RC%
