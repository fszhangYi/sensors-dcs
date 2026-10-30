@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title sensors-dcs gello slice :7011
call "%~dp0..\scripts\env_python.bat" || goto :fail

REM Cell C 单硬件切片 — configs\gello.yaml + sensors_g26562.yaml（仅 Gello）
REM YAML dry_run:false；启动强制 --no-dry-run
set "SENSORS_DCS_AUTH_DISABLED="

echo [sensors-dcs] gello slice --no-dry-run  http://127.0.0.1:7011/
echo   config: configs\gello.yaml
sensors-dcs run -c configs/gello.yaml --no-dry-run
set "ERR=%ERRORLEVEL%"
goto :end

:fail
set "ERR=1"
:end
echo.
echo Exit code %ERR%. Press any key to close.
pause >nul
exit /b %ERR%
