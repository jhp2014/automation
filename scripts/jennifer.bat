@echo off
REM jennifer.bat - jobs.jennifer (Fail-Fast)
chcp 65001 > nul
setlocal
pushd "%~dp0.."
".venv\Scripts\python.exe" -m jobs.jennifer %*
set "RC=%errorlevel%"
popd
echo.
echo (exit code: %RC%)
pause
exit /b %RC%
