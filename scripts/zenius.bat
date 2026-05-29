@echo off
REM zenius.bat - jobs.zenius
chcp 65001 > nul
setlocal
pushd "%~dp0.."
".venv\Scripts\python.exe" -m jobs.zenius %*
set "RC=%errorlevel%"
popd
echo.
echo (exit code: %RC%)
pause
exit /b %RC%
