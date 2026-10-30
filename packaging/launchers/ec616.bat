@echo off
REM Elite EC616 full cell (live) ??configs/full_cell_plus.yaml
setlocal EnableExtensions
cd /d "%~dp0"

set "CFG=%APPDATA%\sensors-dcs\configs\full_cell_plus.yaml"
if not exist "%CFG%" set "CFG=%~dp0_internal\configs\full_cell_plus.yaml"
if not exist "%CFG%" (
  echo error: config not found:
  echo   %%APPDATA%%\sensors-dcs\configs\full_cell_plus.yaml
  echo   %~dp0_internal\configs\full_cell_plus.yaml
  exit /b 1
)

echo [ec616] config=%CFG%
"%~dp0sensors-dcs.exe" -c "%CFG%"
exit /b %ERRORLEVEL%
