@echo off
REM Synthetic dry-run demo ??configs/default.yaml (dry_run: true)
setlocal EnableExtensions
cd /d "%~dp0"

set "CFG=%APPDATA%\sensors-dcs\configs\default.yaml"
if not exist "%CFG%" set "CFG=%~dp0_internal\configs\default.yaml"
if not exist "%CFG%" (
  echo error: config not found:
  echo   %%APPDATA%%\sensors-dcs\configs\default.yaml
  echo   %~dp0_internal\configs\default.yaml
  exit /b 1
)

echo [dry-run] config=%CFG%
"%~dp0sensors-dcs.exe" -c "%CFG%"
exit /b %ERRORLEVEL%
