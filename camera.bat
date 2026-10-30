@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title sensors-dcs camera slice :7011
call "%~dp0..\scripts\env_python.bat" || goto :fail

REM Cell C 单硬件切片 — configs\camera.yaml + sensors_g26562.yaml（仅相机）
REM YAML dry_run:false；启动强制 --no-dry-run
set "SENSORS_DCS_AUTH_DISABLED="

echo [sensors-dcs] camera slice --no-dry-run  http://127.0.0.1:7011/
echo   config: configs\camera.yaml
sensors-dcs run -c configs/camera.yaml --no-dry-run
set "ERR=%ERRORLEVEL%"
goto :end

:fail
set "ERR=1"
:end
echo.
echo Exit code %ERR%. Press any key to close.
pause >nul
exit /b %ERR%
