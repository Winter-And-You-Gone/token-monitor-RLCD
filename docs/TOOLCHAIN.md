# 工具链清单（Windows / ESP32-S3）

本文件记录本机烧录 + 开发所需的全部工具的**确切路径**，供 AI agent 和人类在新对话/新会话里快速定位。
AI agent 入口规则见 [`../AGENTS.md`](../AGENTS.md)。

最近核实日期：2026-06-20。

---

## 1. ESP-IDF 工具链（EIM 统一安装在 `X:\ESP`）

由 Espressif 官方 **EIM**（ESP-IDF Manager）一次性安装，配置文件：
`X:\ESP\eim_config.toml`（target=esp32s3，idf_version=v5.5.1）。

### 1.1 环境变量（cmd / PowerShell 都要先设这些再调 idf.py）

| 变量 | 值 |
|------|-----|
| `IDF_PATH` | `X:\ESP\v5.5.1\esp-idf` |
| `IDF_TOOLS_PATH` | `X:\ESP` |
| `IDF_PYTHON_ENV_PATH` | `X:\ESP\python_env\idf5.5_py3.12_env` |

最简单的做法是 `call %IDF_PATH%\export.bat`（cmd）或 `. $env:IDF_PATH\export.ps1`（PowerShell），
它会自动把下面所有工具拼进 PATH 并校验。项目脚本 `build_flash.bat` / `build_flash.ps1` 就是这么做的。

### 1.2 工具二进制路径（不用 export 时的绝对路径）

| 工具 | 路径 | 版本 |
|------|------|------|
| **idf.py 入口** | `X:\ESP\v5.5.1\esp-idf\tools\idf.py` | 5.5.1 |
| Python 解释器 | `X:\ESP\python_env\idf5.5_py3.12_env\Scripts\python.exe` | 3.12.0 |
| **esptool**（直烧用） | `X:\ESP\python_env\idf5.5_py3.12_env\Scripts\esptool.exe` | 随 idf |
| idf-monitor / idf-size | `X:\ESP\python_env\idf5.5_py3.12_env\Scripts\idf-monitor.exe` 等 | 随 idf |
| Xtensa 交叉编译器（esp32s3 用这个） | `X:\ESP\tools\xtensa-esp-elf\esp-14.2.0_20241119\xtensa-esp-elf\bin` | 14.2.0 |
| RISC-V 交叉编译器（ULP 等） | `X:\ESP\tools\riscv32-esp-elf\esp-14.2.0_20241119\riscv32-esp-elf\bin` | 14.2.0 |
| ESP32 ULP 工具链 | `X:\ESP\tools\esp32ulp-elf\2.38_20240113\esp32ulp-elf\bin` | 2.38 |
| CMake | `X:\ESP\tools\cmake\3.30.2\bin` | 3.30.2 |
| Ninja | `X:\ESP\tools\ninja\1.12.1\ninja.exe` | 1.12.1 |
| ccache | `X:\ESP\tools\ccache\4.11.2\ccache-4.11.2-windows-x86_64\ccache.exe` | 4.11.2 |
| dfu-util | `X:\ESP\tools\dfu-util\0.11\dfu-util-0.11-win64\dfu-util.exe` | 0.11 |
| esp-clang（clang-tidy 可选） | `X:\ESP\tools\esp-clang\esp-19.1.2_20250312\esp-clang\bin` | 19.1.2 |
| esp-clang-libs | `X:\ESP\tools\esp-clang-libs\esp-19.1.2_20250312` | 19.1.2 |
| OpenOCD（JTAG 调试，可选） | `X:\ESP\tools\openocd-esp32\v0.12.0-esp32-20250707\openocd-esp32\bin` | 0.12.0-esp32 |

> ⚠️ **`build_flash.bat` / `build_flash.ps1` 在本机都走不通 `export.bat`**：
> `X:\ESP` 的工具链由 EIM 安装，但**没装 GDB**（`xtensa-esp-elf-gdb`、`riscv32-esp-elf-gdb`）、
> `idf-exe`、`esp-rom-elfs`。`export.bat` 会严格校验这些工具，缺失就拒绝激活
> （报 `tool ... has no installed versions`）。编译 esp32s3 其实用不到 GDB（那是调试器），
> 但 export 不放行就 `idf.py` 进不了 PATH。
>
> 另一个坑：从 git-bash 调 `cmd.exe /c` 跑 bat 时，`MSYSTEM` 环境变量会漏进 cmd 子进程，
> `export.bat` 第 2 行 `if defined MSYSTEM` 会直接拒绝（"This .bat file is for Windows CMD.EXE shell only."）。
>
> **实测可用的编译方式：手动拼 PATH，绕过 export.bat**（见下面 1.3 节的可靠方法）。
> `build_flash.ps1` 的版本号也已过时（见 git 历史），即便 PATH 拼对也建议用 1.3 的方法。

### 1.3 编译固件（本机可靠方法）

由于 `export.bat` 缺 GDB 走不通，用**手动 PATH + 直接调 idf.py** 的方式。从纯 cmd 跑
（不要从 git-bash 嵌套，避免 MSYSTEM 污染）：

```bat
@echo off
set MSYSTEM=
set IDF_PATH=X:\ESP\v5.5.1\esp-idf
set IDF_PYTHON_ENV_PATH=X:\ESP\python_env\idf5.5_py3.12_env
set PATH=X:\ESP\python_env\idf5.5_py3.12_env\Scripts;X:\ESP\tools\xtensa-esp-elf\esp-14.2.0_20241119\xtensa-esp-elf\bin;X:\ESP\tools\riscv32-esp-elf\esp-14.2.0_20241119\riscv32-esp-elf\bin;X:\ESP\tools\esp32ulp-elf\2.38_20240113\esp32ulp-elf\bin;X:\ESP\tools\cmake\3.30.2\bin;X:\ESP\tools\ninja\1.12.1;X:\ESP\tools\ccache\4.11.2\ccache-4.11.2-windows-x86_64;X:\ESP\tools\dfu-util\0.11\dfu-util-0.11-win64;%PATH%
cd /d "X:\ESP32-S3 RLCD\token-monitor-RLCD\firmware"
"%IDF_PYTHON_ENV_PATH%\Scripts\python.exe" "%IDF_PATH%\tools\idf.py" build
```

- `ESP_ROM_ELF_DIR` 未定义的 gdbinit 警告可忽略（只影响 GDB 调试符号，不阻碍编译/链接）。
- 全量编译约 1500 个目标，首次 3-5 分钟；增量很快。
- 旧 `build\CMakeCache.txt` 若被 `build_flash.bat` 删过，会触发完整重配（含 bootloader 子项目）。

### 1.4 串口 / 烧录

- ESP32-S3-RLCD-4.2 以 USB-Serial/JTAG 出现为 `COMx`（设备管理器看 "USB 串行设备"）。
  本机实测为 **COM11**（MAC `58:e6:c5:67:be:c4`）。首次打开偶发 "port busy"，重试即可。
- 烧录（build 完成后，在 `firmware\build` 目录）：
  ```
  "X:\ESP\python_env\idf5.5_py3.12_env\Scripts\esptool.exe" --chip esp32s3 -p COM11 -b 460800 --before default-reset --after hard-reset write_flash @flash_args
  ```
  `flash_args` 内容：bootloader @0x0、app @0x10000、partition-table @0x8000。
- 改过分区表（`partitions.csv`）后必须重烧 partition-table 段，否则 size check 会用旧分区失败。

---

## 2. uv（bridge 的 Python 包管理）

uv **不在** `X:\ESP`，是用户级 pip 安装，两个副本都可用：

| 路径 | 说明 |
|------|------|
| `C:\Users\Winter\AppData\Roaming\uv\venv\Scripts\uv.exe` | uv 自管的 venv（推荐，`uv self update` 升级它） |
| `C:\Users\Winter\AppData\Roaming\Python\Scripts\uv.exe` | pip 装的入口 |

当前版本：uv 0.11.21（2026-06-11）。

bridge 依赖锁在 `bridge\uv.lock`，进入 `bridge\` 后：

```bat
uv sync                 :: 按 lock 装依赖到 .venv
uv run python bridge.py :: 跑 bridge
uv run pytest           :: 跑测试
uv run ruff check .     :: lint（若 pyproject 里配了）
```

---

## 3. 系统级 Python（可选，仅参考）

`where python` 还会看到这些，**别**拿它们跑 idf.py 或 bridge：
- `C:\Python312\python.exe`（系统 3.12）
- `F:\Anaconda\python.exe`
- `C:\Users\Winter\AppData\Local\Programs\Python\Python314\python.exe`
- `C:\Users\Winter\AppData\Local\Microsoft\WindowsApps\python.exe`（微软商店 stub）

idf 用 `X:\ESP\python_env\idf5.5_py3.12_env` 里那个独立 venv；bridge 用 `uv` 自建的 `.venv`。

---

## 4. 重装 / 迁移指引

如果 `X:\ESP` 不见了（换机、重装），按以下步骤重建：

1. 下载 EIM：https://dl.espressif.com/dl/eim/ （Windows 版）。
2. 把本仓库这份 `X:\ESP\eim_config.toml`（备份在 git 历史或本文件里）放回 `X:\ESP\`。
   - 关键字段：`target=["esp32s3"]`、`idf_versions=["v5.5.1"]`、
     `idf_tools=["xtensa-esp-elf","riscv32-esp-elf","esp32ulp-elf","cmake","openocd-esp32","ninja","ccache","dfu-util","esp-rom-elfs"]`。
3. 运行 `eim install`（非交互），它会按 `eim_config.toml` 把工具装回 `X:\ESP\tools`、
   建好 `X:\ESP\python_env\idf5.5_py3.12_env`、解压 idf 到 `X:\ESP\v5.5.1\esp-idf`。
4. uv 单独装：`pip install uv` 或 `irm https://astral.sh/uv/install.ps1 | iex`。

装完路径与本文件表格一致即可；若 EIM 装出了更新版本，记得回填本表的「版本」列。
