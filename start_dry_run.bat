@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title sensors-dcs dry-run :7011
call "%~dp0..\scripts\env_python.bat" || goto :fail

set "SENSORS_DCS_AUTH_DISABLED=1"

echo [sensors-dcs] dry-run  http://127.0.0.1:7011/
sensors-dcs run -c configs/default.yaml --dry-run
set "ERR=%ERRORLEVEL%"
goto :end

:fail
set "ERR=1"
:end
echo.
echo Exit code %ERR%. Press any key to close.
pause >nul
exit /b %ERR%
