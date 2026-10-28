@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title sensors-dcs run :7011
call "%~dp0..\scripts\env_python.bat" || goto :fail

REM Hardware collect — YAML dry_run must be false; do NOT pass --dry-run
set "SENSORS_DCS_AUTH_DISABLED="

echo [sensors-dcs] run --no-dry-run  http://127.0.0.1:7011/
sensors-dcs run -c configs/default.yaml --no-dry-run
set "ERR=%ERRORLEVEL%"
goto :end

:fail
set "ERR=1"
:end
echo.
echo Exit code %ERR%. Press any key to close.
pause >nul
exit /b %ERR%
