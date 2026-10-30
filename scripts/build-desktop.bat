@echo off
REM Native Windows desktop packaging for sensors-dcs (no Wine).
REM Usage:
REM   scripts\build-desktop.bat
REM   scripts\build-desktop.bat --target native-win
REM   scripts\build-desktop.bat --no-delta
setlocal EnableExtensions
cd /d "%~dp0.."

if not defined PIP_INDEX_URL set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
REM Optional CSV override; build_desktop.py also falls back aliyun/tencent/pypi.org on SSL fail.
if not defined PIP_INDEX_URLS set "PIP_INDEX_URLS="
if not defined SENSORS_DCS_BUILD_TMP set "SENSORS_DCS_BUILD_TMP=%CD%\.tools\build-tmp"

if not exist "sensors\src\sensors" (
  echo.
  echo ERROR: missing .\sensors junction to hik-sensors
  echo   cd /d "%CD%"
  echo   mklink /J sensors ..\sensors
  echo.
  pause
  exit /b 1
)

REM Prefer embed 3.10, then workspace venv, then PATH "python".
set "PY="
if exist "%CD%\.tools\win-python\python.exe" set "PY=%CD%\.tools\win-python\python.exe"
if not defined PY if exist "%CD%\..\.venv\Scripts\python.exe" set "PY=%CD%\..\.venv\Scripts\python.exe"
if not defined PY if exist "%CD%\.venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
if not defined PY set "PY=python"

echo ==^> sensors-dcs desktop build ^(native-win^)
echo     root:      %CD%
echo     python:    %PY%
echo     pip index: %PIP_INDEX_URL% (auto-fallback mirrors on SSL fail)
echo     build tmp: %SENSORS_DCS_BUILD_TMP%
echo.

"%PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 1>nul 2>nul
if errorlevel 1 (
  echo.
  echo ERROR: cannot run Python: %PY%
  echo   Keep .tools\win-python from a prior build, or put python on PATH.
  echo.
  pause
  exit /b 1
)

if "%~1"=="" (
  "%PY%" "%CD%\scripts\build_desktop.py" --target native-win --skip-frontend
) else (
  "%PY%" "%CD%\scripts\build_desktop.py" --skip-frontend %*
)
set "ERR=%ERRORLEVEL%"

echo.
if not "%ERR%"=="0" (
  echo BUILD FAILED  exit=%ERR%
) else (
  echo BUILD OK
)
pause
exit /b %ERR%
