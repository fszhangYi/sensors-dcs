@echo off
REM Elite CS66 / G26562 cell (live) ??configs/g26562.yaml
setlocal EnableExtensions
cd /d "%~dp0"

set "CFG=%APPDATA%\sensors-dcs\configs\g26562.yaml"
if not exist "%CFG%" set "CFG=%~dp0_internal\configs\g26562.yaml"
if not exist "%CFG%" (
  echo error: config not found:
  echo   %%APPDATA%%\sensors-dcs\configs\g26562.yaml
  echo   %~dp0_internal\configs\g26562.yaml
  exit /b 1
)

echo [cs66] config=%CFG%
"%~dp0sensors-dcs.exe" -c "%CFG%"
exit /b %ERRORLEVEL%
