@echo off
REM ===================================================================
REM  Build the RLCD firmware (ESP32-S3, ESP-IDF v5.5.1).
REM
REM  Toolchain: X:\ESP  (esp-idf v5.5.1 + all tools + python env).
REM  See docs/TOOLCHAIN.md for the full layout.
REM
REM  Usage:  scripts\build-firmware.bat            (full/incremental build)
REM          scripts\build-firmware.bat clean      (wipe build dir first)
REM ===================================================================
setlocal

set IDF_TOOLS_PATH=X:\ESP
set IDF_PATH=X:\ESP\v5.5.1\esp-idf

REM Clear MSYSTEM: git-bash leaks it into cmd children and export.bat
REM refuses to run ("for Windows CMD.EXE shell only").
set MSYSTEM=

call "%IDF_PATH%\export.bat"
if errorlevel 1 (echo [build] export.bat failed & exit /b 1)

cd /d "%~dp0..\firmware"
if /i "%~1"=="clean" if exist build rmdir /s /q build

idf.py build
set RC=%errorlevel%
if "%RC%"=="0" (
  echo.
  echo [build] OK -^> firmware\build\rlcd_dashboard.bin
) else (
  echo.
  echo [build] FAILED with code %RC%
)
exit /b %RC%
