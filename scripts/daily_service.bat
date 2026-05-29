@echo off
REM daily_service.bat - jobs.daily_service
chcp 65001 > nul
setlocal
pushd "%~dp0.."
".venv\Scripts\python.exe" -m jobs.daily_service %*
set "RC=%errorlevel%"
popd
echo.
echo (exit code: %RC%)
pause
exit /b %RC%
