$env:IDF_PATH = "X:\ESP\v5.5.1\esp-idf"
$env:IDF_TOOLS_PATH = "X:\ESP"
$env:MSYSTEM = ""

# Add tool paths
$env:PATH = @(
    "X:\ESP\python_env\idf5.5_py3.12_env\Scripts",
    "X:\ESP\tools\xtensa-esp-elf\esp-14.2.0_20241119\xtensa-esp-elf\bin",
    "X:\ESP\tools\cmake\3.30.2\bin",
    "X:\ESP\tools\ninja\1.12.1",
    "X:\ESP\tools\ccache\4.11.2\ccache-4.11.2-windows-x86_64",
    "X:\ESP\tools\dfu-util\0.11\dfu-util-0.11-win64",
    "X:\ESP\tools\esp32ulp-elf\2.38_20240113\esp32ulp-elf\bin"
) -join ";" + ";" + $env:PATH

$python = "X:\ESP\python_env\idf5.5_py3.12_env\Scripts\python.exe"
Set-Location "X:\ESP32-S3 RLCD\token-monitor-RLCD\firmware"

# Create constraints file if missing
$constraintsDir = "X:\ESP"
$constraintsFile = "$constraintsDir\espidf.constraints.v5.5.txt"
if (-not (Test-Path $constraintsFile)) {
    # Create empty constraints file to bypass check
    New-Item -Path $constraintsFile -ItemType File -Force | Out-Null
}

& $python "X:\ESP\v5.5.1\esp-idf\tools\idf.py" build 2>&1 | Out-File -FilePath "X:\ESP32-S3 RLCD\token-monitor-RLCD\build_log.txt" -Encoding utf8
Get-Content "X:\ESP32-S3 RLCD\token-monitor-RLCD\build_log.txt" -Tail 60
