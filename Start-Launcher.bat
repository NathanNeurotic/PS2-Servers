@echo off
REM Launch the PS2 Servers GUI from source (Windows).
REM The packaged .exe will not need Python; this script is for running from source.
cd /d "%~dp0"
python -m launcher
if errorlevel 9009 (
  echo.
  echo Python 3 was not found on your PATH.
  echo Install it from https://www.python.org/downloads/ and try again.
  pause
)
