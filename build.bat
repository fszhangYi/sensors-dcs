@echo off
REM Convenience entry: Windows native desktop packaging.
REM   build.bat
REM   build.bat --no-delta
cd /d "%~dp0"
call "%~dp0scripts\build-desktop.bat" %*
set "ERR=%ERRORLEVEL%"
REM build-desktop.bat already pauses; keep exit code for callers.
exit /b %ERR%
