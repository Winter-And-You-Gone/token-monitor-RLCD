# AGENTS.md — 给 AI agent 的项目规则

本文件是任何在本仓库工作的 AI agent（Claude Code / Codex / Cursor 等）的必读入口。
详细工具链清单见 [`docs/TOOLCHAIN.md`](docs/TOOLCHAIN.md)。

## 项目概览

- **硬件**：Waveshare ESP32-S3-RLCD-4.2（反射式 LCD 桌面摆件），目标芯片 **ESP32-S3**。
- **固件**：`firmware/`，基于 ESP-IDF 5.5.1，LVGL UI。
- **桥接**：`bridge/`，Python 守护进程（解析 Claude/DeepSeek 用量、天气），通过 `:7777` 给设备提供 JSON。
- **目标平台**：开发/烧录在 **Windows**（`cmd.exe` / PowerShell），bridge 也可在 Linux 上以 systemd 跑。

## 工具链位置（关键！每次新对话都要先确认）

### ESP-IDF 工具链（一次性由 EIM 安装在 `X:\ESP`）

> ⚠️ **不要**假设 `idf.py` / `esptool` / 交叉编译器在 PATH 上——它们不在。
> 必须先 source `export.bat`（cmd）或 `export.ps1`（PowerShell）把环境变量设好，再用绝对路径或项目脚本。

| 变量 | 值 |
|------|-----|
| `IDF_PATH` | `X:\ESP\v5.5.1\esp-idf` |
| `IDF_TOOLS_PATH` | `X:\ESP` |
| `IDF_PYTHON_ENV_PATH` | `X:\ESP\python_env\idf5.5_py3.12_env`（Python 3.12.0） |

各工具确切子目录见 [`docs/TOOLCHAIN.md`](docs/TOOLCHAIN.md) 的「工具二进制路径」表。
最常用的：
- idf.py 入口：`X:\ESP\v5.5.1\esp-idf\tools\idf.py`
- esptool 直烧：`X:\ESP\python_env\idf5.5_py3.12_env\Scripts\esptool.exe`
- Xtensa 交叉编译器：`X:\ESP\tools\xtensa-esp-elf\esp-14.2.0_20241119\xtensa-esp-elf\bin`

### uv（bridge 的 Python 包管理）

- **不在** `X:\ESP`，是用户级安装：
  - `C:\Users\Winter\AppData\Roaming\uv\venv\Scripts\uv.exe`（v0.11.21，推荐）
  - 备用：`C:\Users\Winter\AppData\Roaming\Python\Scripts\uv.exe`
- bridge 目录 `bridge/` 下有 `pyproject.toml` + `uv.lock`，进入后用 `uv sync` 装依赖、`uv run` 执行。

## 一键脚本（⚠️ 本机走不通 export.bat，见下）

项目根目录有两个脚本，但**都不能直接用**：

- `build_flash.bat` — 走 `export.bat`，但 `X:\ESP` 缺 GDB/idf-exe/esp-rom-elfs，export 严格校验失败；从 git-bash 调还会被 `MSYSTEM` 检测拦掉。
- `build_flash.ps1` — 手动拼 PATH，思路对，但版本号过时且只 build 不 flash。

**编译固件用 `docs/TOOLCHAIN.md` 1.3 节的手动 PATH 方法**（纯 cmd 跑，已验证可用）。
**烧录用 esptool 直烧**（COM11，见 `docs/TOOLCHAIN.md` 1.4 节）：
```
"X:\ESP\python_env\idf5.5_py3.12_env\Scripts\esptool.exe" --chip esp32s3 -p COM11 -b 460800 --before default-reset --after hard-reset write_flash @flash_args
```
（在 `firmware\build` 目录跑，先 build 过才有 `flash_args`。）

## 常用命令

### 固件（在 `firmware/` 目录，且已 export IDF 环境）

```bat
idf.py set-target esp32s3
idf.py build
idf.py -p COMx flash monitor    :: Ctrl+] 退出监视器
idf.py fullclean                :: 彻底清理（删 build/）
```

### bridge（在 `bridge/` 目录）

```bat
uv sync                 :: 安装依赖
uv run python bridge.py :: 本地跑 bridge
uv run pytest           :: 跑测试
```

### 更新 gif 动画（强制流程，见工作约定第 6 条）

三个 gif 目录的性质（别搞混）：

| 目录 | 尺寸 | 颜色 | 用途 |
|------|------|------|------|
| `clawd-on-desk/assets/gif/` | 302×300 | **彩色**（上游源） | 上游参考项目源，**不能动**；生成脚本仅在 newgif 没有同名 gif 时回退读它 |
| `bridge/assets/newgif/` | 302×300 | **黑白** | 重新渲染的黑白稿，**生成脚本优先读它**做固件帧数据（按需，只放有变化的） |
| `bridge/assets/clawd_rlcd/size-56/gifs/` | 56×56 | 黑白 | 缩放到 56px 的展示版，sim.html 模拟器/预览用 |

更新动画的完整流程：

1. **把新黑白 gif 放进 newgif**：重新渲染的黑白 gif（302×300 左右原始尺寸）放进 `bridge/assets/newgif/`。
2. **同步到 size-56**：把同一份 gif 也复制到 `bridge/assets/clawd_rlcd/size-56/gifs/`（模拟器预览用，尺寸会被缩放）。
3. **生成脚本优先读 newgif**：`scripts/gen_pet_anim.py` / `gen_pet_big_anim.py` 的解析逻辑是「newgif 有同名 gif 优先读 newgif（黑白稿），否则回退 clawd-on-desk/assets/gif/（彩色源）」。已在脚本里实现（`NEWGIF_DIR`）。
4. **重跑两个生成脚本**：`python scripts/gen_pet_anim.py` + `python scripts/gen_pet_big_anim.py`，重写 `pet_anim.c` / `pet_big_anim.c`（脚本内部把 302px 黑白稿栅格化缩放到 56/184px 固件帧）。
5. **重新编译烧录**：固件帧数据才会用上新 gif。

⚠️ **绝不改动 `clawd-on-desk/assets/gif/` 里的 gif**——那是上游彩色源素材。新黑白 gif 只进 `newgif/` 和 `size-56/gifs/`。

> 注：newgif 和 size-56 **都是黑白**。区别是 newgif 保持原始尺寸(供生成脚本栅格化)，size-56 是已缩放到 56px 的展示版(供模拟器)。彩色原版只在 clawd-on-desk，生成脚本平时读不到它（除非 newgif 缺该 gif）。

## 工作约定

1. **改固件前**先 `idf.py build` 确认基线能过，再动代码。
2. **改 bridge 前**先 `uv run pytest` 确认基线。
3. 路径里有空格（`X:\ESP32-S3 RLCD\...`），shell 命令务必加引号。
4. 不要把 `build/`、`uv.lock` 之外的生成物、`*.rlcd-bak-*` 备份文件提交进去。
5. 烧录是物理设备操作，确认串口号（COMx）再执行，不要瞎试端口。
6. **更新 gif 动画时**：`newgif/` 里的新 gif → 复制到 `size-56/gifs/` + 确认生成脚本优先读 `newgif/` + 重跑两个生成脚本 + 重新编译烧录。**不要动 `clawd-on-desk/` 里的 gif。**
7. **zcode 动画触发**：已通过项目根的 `rlcd-pet-zcode/` 插件接入（不走 settings.json hooks，而是 zcode 的 `plugins.dirs` 机制）。该插件的 `hooks/run-hook.cmd` 把 zcode 生命周期事件转发给 `bridge/pet_hook.js --agent zcode`——**事件→状态映射的单一真相源是 `pet_hook.js`**，改映射改它一处即可。zcode 与 Claude/Codex 并发时会自动走 working tier 分档（`sessions>=2` → `headphones-groove`）。启用/卸载步骤见 `rlcd-pet-zcode/README.md`。

## 当工具链找不到时

如果上述路径不存在（机器迁移/重装），重建步骤见 [`docs/TOOLCHAIN.md`](docs/TOOLCHAIN.md) 末尾「重装/迁移指引」。
核心是用 Espressif 官方 **EIM** 安装器，配置文件 `X:\ESP\eim_config.toml` 已记录所有选项。
