@echo off
REM ===================================================================
REM  Flash the built RLCD firmware to the ESP32-S3-RLCD-4.2.
REM
REM  Toolchain: X:\ESP  (see docs/TOOLCHAIN.md)
REM
REM  The COM port is NOT fixed - detect it by USB VID 0x303A
REM  (Espressif USB-Serial/JTAG) instead of hardcoding.
REM
REM  Usage:  scripts\flash-firmware.bat            (build/ must exist)
REM          scripts\flash-firmware.bat COM7       (override the port)
REM          scripts\flash-firmware.bat --erase    (erase_flash first,
REM                                                needed after a
REM                                                partitions.csv change)
REM ===================================================================
setlocal

set IDF_TOOLS_PATH=X:\ESP
set IDF_PATH=X:\ESP\v5.5.1\esp-idf
set PY=%IDF_TOOLS_PATH%\python_env\idf5.5_py3.13_env\Scripts\python.exe
set ESPTOOL=%IDF_TOOLS_PATH%\python_env\idf5.5_py3.13_env\Scripts\esptool.exe

cd /d "%~dp0..\firmware"
if not exist build\flash_args (
  echo [flash] no build found - run scripts\build-firmware.bat first
  exit /b 1
)

set PORT=%~1
set DO_ERASE=0
if /i "%~1"=="--erase" (set PORT= & set DO_ERASE=1)
if /i "%~2"=="--erase" set DO_ERASE=1

if "%PORT%"=="" (
  for /f "delims=" %%p in ('"%PY%" _detect_port.py') do set PORT=%%p
)
if "%PORT%"=="" (
  echo [flash] no ESP32-S3 USB-Serial/JTAG device found ^(VID 0x303A^).
  echo [flash] plug the board in, then retry - do NOT guess a port.
  exit /b 1
)
echo [flash] using port %PORT%

if %DO_ERASE%==1 (
  echo [flash] erasing flash first ...
  "%ESPTOOL%" --chip esp32s3 -p %PORT% erase_flash
  if errorlevel 1 exit /b 1
)

cd build
"%ESPTOOL%" --chip esp32s3 -p %PORT% -b 460800 --before default_reset --after hard_reset write_flash @flash_args
if errorlevel 1 (echo [flash] FAILED & exit /b 1)
echo [flash] done.
