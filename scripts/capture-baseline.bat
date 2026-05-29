@echo off
REM capture-baseline.bat - baseline image + marker only (layout validation still runs)
chcp 65001 > nul
setlocal
pushd "%~dp0.."
".venv\Scripts\python.exe" -m jobs.capture --make-baseline %*
set "RC=%errorlevel%"
popd
echo.
echo (exit code: %RC%)
pause
exit /b %RC%
