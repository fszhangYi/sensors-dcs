@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title sensors-dcs G26562 slave-right :7011
call "%~dp0..\scripts\env_python.bat" || goto :fail

REM 本机 = slave-right：右 D435 @90Hz，连主站 192.168.1.77:7010
REM 配置对：configs\g26562.yaml + configs\sensors_g26562.yaml
REM 同步到 %%APPDATA%%\sensors-dcs\configs（桌面壳 / active 指针）
set "SENSORS_DCS_AUTH_DISABLED="
set "APP_CFG=%APPDATA%\sensors-dcs\configs"
if not exist "%APP_CFG%" mkdir "%APP_CFG%"
copy /Y "%~dp0configs\g26562.yaml" "%APP_CFG%\g26562.yaml" >nul
copy /Y "%~dp0configs\sensors_g26562.yaml" "%APP_CFG%\sensors_g26562.yaml" >nul
> "%APP_CFG%\active_config.path" echo %APP_CFG%\g26562.yaml

echo [sensors-dcs] slave-right  http://127.0.0.1:7011/
echo   station: cell-c-g26562-right  advertise=192.168.1.33  master=192.168.1.77:7010
echo   config:  configs\g26562.yaml + configs\sensors_g26562.yaml
echo   camera:  cam-g26562-right  640x480@90
sensors-dcs run -c "%~dp0configs\g26562.yaml" --no-dry-run
set "ERR=%ERRORLEVEL%"
goto :end

:fail
set "ERR=1"
:end
echo.
echo Exit code %ERR%. Press any key to close.
pause >nul
exit /b %ERR%
