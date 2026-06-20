@echo off
set IDF_PATH=X:\ESP\v5.5.1\esp-idf
set IDF_TOOLS_PATH=X:\ESP
set IDF_PYTHON_ENV_PATH=X:\ESP\python_env\idf5.5_py3.12_env
call %IDF_PATH%\export.bat >nul 2>&1
cd /d X:\ESP32-S3 RLCD\token-monitor-RLCD\firmware
if exist build\CMakeCache.txt del /q build\CMakeCache.txt
echo BUILD_START > "X:\ESP32-S3 RLCD\token-monitor-RLCD\build_status.txt"
idf.py build >> "X:\ESP32-S3 RLCD\token-monitor-RLCD\build_log.txt" 2>&1
if %ERRORLEVEL% EQU 0 (
    echo BUILD_OK >> "X:\ESP32-S3 RLCD\token-monitor-RLCD\build_status.txt"
) else (
    echo BUILD_FAIL >> "X:\ESP32-S3 RLCD\token-monitor-RLCD\build_status.txt"
)
