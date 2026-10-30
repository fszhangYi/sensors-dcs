@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title sensors-dcs G26562 :7011
call "%~dp0..\scripts\env_python.bat" || goto :fail

REM Cell C / G26562 live pack — configs\cs66.yaml + sensors_g26562.yaml
REM YAML dry_run:false；启动强制 --no-dry-run
set "SENSORS_DCS_AUTH_DISABLED="

echo [sensors-dcs] G26562 run --no-dry-run  http://127.0.0.1:7011/
echo   config: configs\cs66.yaml
sensors-dcs run -c configs/cs66.yaml --no-dry-run
set "ERR=%ERRORLEVEL%"
goto :end

:fail
set "ERR=1"
:end
echo.
echo Exit code %ERR%. Press any key to close.
pause >nul
exit /b %ERR%
