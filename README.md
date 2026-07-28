# token-monitor-RLCD

[English](README.en.md)

把你的 Claude、Codex 和 DeepSeek 实时用量显示在 Waveshare ESP32-S3-RLCD-4.2 反射式 LCD 上的桌面摆件。

## 实现逻辑

```
~/.claude/**/*.jsonl   （Claude Code 会话日志，本地写入）
         │
         ▼
   bridge 守护进程                            ESP32-S3-RLCD-4.2
   ──────────────                            ─────────────────
   • 调用 ccusage 解析本地用量               • 开机连接 Wi-Fi
   • 汇总 Claude / Codex 今日、本月、总量     • 每 60 秒 GET /api/usage
                                             • 用 cJSON 解析 JSON
   • 获取 DeepSeek 账户余额                  • LVGL 双栏 UI：
   • 获取室外天气（国内城市优先 CMA，免 key）    轮播 Claude / DeepSeek / Codex
   • 接收 AI agent 生命周期事件驱动宠物动画      顶部小宠物 + 大动画页
   • 缓存结果，在 :7777 提供 JSON 服务
                                             • 读取室内温湿度（SHTC3）
                                             • NTP 对时（CST-8）显示时间
```

bridge 以 systemd `--user` 服务形式运行在与 Claude Code / Codex 同一台机器上。后台线程每 45 秒刷新一次 ccusage，使 ESP32 的 HTTP 请求始终从缓存秒返（ccusage 冷启动约需 10 秒）。宠物状态由 `bridge/pet_hook.js` 接收各 agent 生命周期事件后合并进 `/api/usage`。

```
14:30                            ☁  24°C
IN 26.3°C  65%RH         SHENZHEN  Partly
──────────────────────────────────────────
 CLAUDE           │  DEEPSEEK
 opus       12.9M │      可用
 sonnet      4.4M │    ¥ 70.79
 ─────────────────│──────────────────────
 今日   382K  $9.14│ 送值        0.00
 本月   8.4M   $187│ 充值       70.79
 合计  18.2M   $214│ 今日token 2.4M
```

## 页面

按 BOOT 键循环切换三个页面：

### Token 仪表盘（默认页）

双栏显示 Claude / DeepSeek / Codex 的今日、本月、合计用量与费用，顶部天气、时间、室内温湿度，底部小宠物动画。

### Clawd 宠物动画

184×184 大尺寸 Clawd 螃蟹动画，展示当前 AI agent 的工作状态。角色衍生于 [clawd-on-desk](https://github.com/rullerzhou-afk/clawd-on-desk) 项目，重新渲染为黑白稿以适配反射式 LCD。

### Codex 雷达

3×3 网格展示 [codexradar.com](https://codexradar.com) 的智力效率基准数据：

- 行：sol（太阳）/ terra（地球）/ luna（月亮）三个模型
- 列：ULTRA / MAX / XHIGH 三档努力程度
- 每格显示 IQ 值、价格、耗时、通过率（/112）及趋势 sparkline
- 每 10 分钟自动刷新，顶部显示下次刷新倒计时


## 硬件

- [Waveshare ESP32-S3-RLCD-4.2](https://www.waveshare.com/wiki/ESP32-S3-RLCD-4.2) — 4.2 英寸反射式 LCD（类纸面），ESP32-S3，Wi-Fi，RTC，温湿度，SD，音频。
- USB-C 数据线（用于烧录）。

## 架构
	
	```
	Linux / macOS 主机                        ESP32-S3-RLCD-4.2
	──────────────                            ─────────────────
	~/.claude/**/*.jsonl                      LVGL 宠物动画 + 仪表盘
	        │                                         ▲
	        ▼                    局域网 HTTP           │
	   bridge 守护进程 ── GET /api/usage (60s) ───────┘
	   （调用 ccusage）
	   :7777
	```
	
	- **Bridge**（`bridge/`）— Python FastAPI 守护进程。调用 `ccusage blocks/daily/monthly --json`，汇总成统一 schema，在 `http://<主机>:7777/api/usage` 提供服务。以 systemd `--user` 方式运行。另有 `bridge/sim.html` 网页模拟器（`/sim` 路径），可在 PC 浏览器中预览 RLCD 渲染效果。
	- **固件**（`firmware/`）— ESP-IDF + LVGL v9 项目。每 60 秒轮询 bridge，在 RLCD 上渲染双栏仪表盘 + 宠物动画（Clawd 小螃蟹）。
	
	## 宠物动画系统
	
	设备在仪表盘下方显示一个 Clawd 小螃蟹动画角色，根据使用状态自动切换动画：
	
	| 状态 | 动画 | 触发条件 |
	|------|------|----------|
	| idle | 看书 | 无活动 / AI agent 空闲 |
	| thinking | 思考气泡 | AI agent 正在思考 |
	| working | 敲键盘 | AI agent 正在输出 |
	| juggling | 抛接球 | agent 并行任务 |
	| headphones-groove | 戴耳机律动 | 多 agent 并发（sessions ≥ 2）|
	| building | 盖楼 | 构建/编译中 |
	| sweeping | 打扫 | 清理/整理 |
	| sleeping | 睡觉 / 打盹 / 打哈欠 | 长时间无活动 |
	| notification | 通知提醒 | 有新通知 |
	| error | 感叹号 | 出错 |
	| happy / carrying / debugger / conducting / bubble | — | 各类事件 |
	
	动画帧数据由 `scripts/gen_pet_anim.py`（56×56 小型动画）和 `scripts/gen_pet_big_anim.py`（184×184 大动画）从黑白 GIF 素材生成 C 语言帧表，编译进固件。
	
	### 动画素材流水线
	
	```
	clawd-on-desk/assets/gif/       ← 上游彩色源素材（不动）
	       │
	       ▼ (重新渲染为黑白稿)
	bridge/assets/newgif/              ← 黑白 GIF（脚本优先读取）
	       │
	       ├──→ bridge/assets/clawd_rlcd/size-184/gifs/  ← 大动画模拟器预览
	       └──→ bridge/assets/clawd_rlcd/size-56/gifs/   ← 小动画模拟器预览
	       │
	       ▼ (栅格化缩放，生成 C 帧表)
	scripts/gen_pet_anim.py → firmware/components/ui_app/pet_anim.c
	scripts/gen_pet_big_anim.py → firmware/components/ui_app/pet_big_anim.c
	```
	
	### ZCode / Claude Code / Codex 集成
	
	AI agent 的事件通过 `rlcd-pet-zcode/` 插件（ZCode 端）或 `bridge/pet_hook.js`（通用端）转发给 bridge，bridge 按事件类型切换到对应动画。
	
	- **`rlcd-pet-zcode/`** — ZCode 插件，通过 `hooks/run-hook.cmd` 将 ZCode 生命周期事件转发给 `pet_hook.js --agent zcode`
	- **`bridge/pet_hook.js`** — 事件→状态映射的单一真相源，支持 `--agent` 参数区分来源
	- 多 agent 并发（sessions ≥ 2）自动走 `headphones-groove` 动画
	
	---

## 部署步骤

### 第一步 — 安装前置依赖

在运行 Claude Code 的机器上（Linux）：

```bash
# 1. uv（Python 包管理器）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Node/npm + 全局 ccusage（bridge 运行时不会调用 npx/@latest）
# Ubuntu/Debian：
sudo apt install nodejs npm
# 或使用 nvm：https://github.com/nvm-sh/nvm
npm install -g ccusage

# 3. 验证 ccusage 可用
ccusage --help
```

### 第二步 — 克隆并本地测试 bridge

```bash
git clone https://github.com/Winter-And-You-Gone/token-monitor-RLCD.git
cd token-monitor-RLCD/bridge

uv sync                            # 安装 Python 依赖（首次）
uv run python bridge.py            # 默认监听 127.0.0.1:7777
```

新开一个终端验证：

```bash
curl http://localhost:7777/api/usage | jq          # 实时数据
curl 'http://localhost:7777/api/usage?mock=1' | jq # 模拟数据（不依赖 ccusage）
```

浏览器打开 `http://localhost:7777/sim` 可以直接使用本机 RLCD 模拟器。它会走同一个 `/api/usage`
接口，支持 mock/live、token、自动刷新和 stale/offline 状态。


### 第三步 — 安装为 systemd 服务

```bash
# 在仓库根目录执行：
scripts/install-bridge-linux.sh
```

脚本会自动创建 `~/.config/systemd/user/rlcd-bridge.service` 并启动服务。该服务会读取
`bridge/.env`；需要让 ESP32 从局域网访问时，在 `.env` 中设置 `RLCD_HOST=0.0.0.0`
并同时设置 `RLCD_AUTH_TOKEN`。

```bash
systemctl --user status rlcd-bridge
journalctl --user -u rlcd-bridge -f
```

如果需要退出登录后继续运行（VPS / 无头服务器）：

```bash
loginctl enable-linger $USER
```

#### 可选环境变量

创建 `bridge/.env`（已在 .gitignore 中）并按需填写：

```ini
RLCD_HOST=127.0.0.1        # 监听地址；局域网访问设 0.0.0.0，并必须设置 token
RLCD_PORT=7777              # 监听端口（默认 7777）
RLCD_AUTH_TOKEN=<随机串>    # 非本地访问时必须设置
RLCD_ALLOW_QUERY_TOKEN=0    # 默认只接受 X-RLCD-Token header；旧客户端需要 ?token= 时才设为 1
RLCD_TZ=Asia/Hong_Kong      # 用量“今日/本月”的切日时区（默认 Asia/Hong_Kong）
RLCD_PET_MOUSE_MONITOR=1    # Windows bridge 端监测鼠标移动，移动时重置/唤醒小螃蟹 idle-sleep 计时
RLCD_PET_MOUSE_POLL_SEC=0.5 # 鼠标位置轮询间隔
RLCD_PET_MOUSE_IDLE_SEC=20   # 小螃蟹空闲多久后播放一次 idle 小动作
RLCD_PET_IDLE_LOOK_SEC=14    # idle 小动作持续时间；默认使用 reading 动画
RLCD_PET_MOUSE_SLEEP_SEC=300 # 无新事件多久后进入打哈欠/打盹/睡觉；原版 Clawd 是 60 秒
RLCD_PET_SLEEP_SEQUENCE=1    # 设为 0 可关闭自动睡眠序列
RLCD_PET_ACTIVE_TTL_SEC=90   # pet 活跃状态超时（秒）
RLCD_PET_COMPLETED_HOLD_SEC=2.0 # completed 状态的保持时间（秒）
RLCD_PET_YAWN_SEC=3.0        # 打哈欠时长（秒）
RLCD_PET_DEEP_SLEEP_SEC=600  # 深睡超时（无事件进入深睡）
RLCD_PET_COLLAPSE_SEC=0.8    # 倒地入睡过渡时长（秒）
RLCD_PET_WAKE_SEC=1.5        # 醒来过渡时长（秒）
RLCD_PET_IDLE_LOOK_ASSET=clawd-idle-reading.svg  # idle 小动作动画素材
RLCD_PET_MOUSE_MIN_DELTA=1.0 # 鼠标移动最小触发距离（像素）
RLCD_PET_MAX_SESSIONS=20     # Codex/Jupyter 最大 session 数
RLCD_CODEX_JSONL_MONITOR=1   # 启用 Codex JSONL 监控
RLCD_CODEX_JSONL_POLL_SEC=1.5 # Codex JSONL 轮询间隔
RLCD_CODEX_JSONL_RECENT_SEC=120 # Codex 最近 session 时间窗口
RLCD_REFRESH_SEC=45          # ccusage 后台刷新间隔（秒）
RLCD_INCLUDE_OTHERS=1        # 仪表盘包含 "others" 分类
RLCD_WEATHER_OVERRIDE_TTL=600 # 天气覆盖数据 TTL（秒）
RLCD_WEATHER_OVERRIDE_RETRY_SEC=30 # 天气覆盖重试间隔
RLCD_WEATHER_CMA=1         # 城市名优先使用中国气象局 CMA 数据；设为 0 可关闭
RLCD_WEATHER_LAT=30.2741   # 坐标兜底纬度（默认杭州）
RLCD_WEATHER_LON=120.1551  # 坐标兜底经度
RLCD_WEATHER_CITY=杭州     # 天气城市；中文城市会返回 city_ascii 供固件显示
# 坐标天气兜底：不设 key 时自动用 Open-Meteo；设置后坐标查询会用彩云天气
CAIYUN_API_KEY=<彩云天气token>
DEEPSEEK_API_KEY=sk-...    # 启用 DeepSeek 余额显示（可选）
```

修改后重启服务：

```bash
systemctl --user restart rlcd-bridge
```

**只要 bridge 不是只监听 loopback，就必须设置 `RLCD_AUTH_TOKEN`。** 生成随机 token：

```bash
openssl rand -hex 32
```

### 第四步 — 编译并烧录固件

#### 前置工具

- [ESP-IDF v5.x](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/get-started/)
- Windows：从 <https://dl.espressif.com/dl/esp-idf/> 下载 **Universal Online Installer**（选最新 v5.x，目标芯片选 `esp32s3`）。

#### Linux / macOS

```bash
cd firmware
cp main/secrets.h.example main/secrets.h
$EDITOR main/secrets.h    # 填入 Wi-Fi SSID/密码、bridge 地址、token

idf.py set-target esp32s3
idf.py build flash monitor    # Ctrl+] 退出串口监视器
```

#### Windows（通过开始菜单 ESP-IDF PowerShell 快捷方式）

```powershell
cd C:\path\to\token-monitor-RLCD\firmware
copy main\secrets.h.example main\secrets.h
notepad main\secrets.h    # 填入 Wi-Fi / bridge 地址 / token
idf.py set-target esp32s3
idf.py build flash monitor
```

#### `secrets.h` 各项说明

| 字段 | 示例 | 说明 |
|------|------|------|
| `RLCD_WIFI_SSID` | `"MyNetwork"` | 仅支持 2.4 GHz（ESP32 不支持 5 GHz） |
| `RLCD_WIFI_PASSWORD` | `"password"` | WPA2 |
| `RLCD_BRIDGE_URL` | `"http://192.168.1.42:7777/api/usage"` | bridge 地址，见下方部署模式 |
| `RLCD_BRIDGE_TOKEN` | `""` | 与 `RLCD_AUTH_TOKEN` 保持一致，未设置则留空 |
| `RLCD_POLL_SEC` | `60` | 轮询间隔（秒） |

首次编译会通过 IDF 组件管理器下载 `lvgl/lvgl@^9.4.0`（约 50 MB），需要联网。

### 第五步 — 验证

1. 串口监视器打印 `connecting to <ssid>...` → `got IP ...`，随后仪表盘填充数据。
2. 建议先用 mock 模式：将 `RLCD_BRIDGE_URL` 改为 `.../api/usage?mock=1` 烧录，确认 UI 正常渲染。
3. 切换回实时模式，跑一分钟 Claude Code / Codex，等下次轮询后观察 `claude.today.tokens_used` 或 `other[].today.tokens_used` 增长。
4. 停止 bridge 服务：UI 应显示 `(stale)` 但不崩溃，保持上次数据。

---

## 部署模式

### 模式 A — 同局域网（最简单）

bridge 和 ESP32 在同一个家庭/办公室网络中。

```ini
# bridge/.env
RLCD_HOST=0.0.0.0
RLCD_AUTH_TOKEN=<随机32字节>
```

```c
// secrets.h
#define RLCD_BRIDGE_URL   "http://192.168.1.42:7777/api/usage"
#define RLCD_BRIDGE_TOKEN "<相同token>"
```

### 模式 B — 公网直连（bridge 在 VPS 上）

将 bridge 直接暴露在 VPS 的公网 IP 上，ESP32 通过公网连接。由于流量经过公网，强 token 和防火墙规则必不可少。

```ini
# VPS 上的 bridge/.env
RLCD_HOST=0.0.0.0
RLCD_AUTH_TOKEN=<随机32字节>
```

```c
// secrets.h
#define RLCD_BRIDGE_URL   "http://203.0.113.10:7777/api/usage"
#define RLCD_BRIDGE_TOKEN "<相同token>"
```

防火墙：仅在需要时开放 7777 端口，或限制来源 IP 为家庭宽带的 IP 段。

#### 进阶：用反向代理套 HTTPS（nginx）

更安全的方案是在 nginx 后面运行 bridge，配合 Let's Encrypt 证书做 TLS 终止。这样无需开放 7777 端口，统一走 443。

```nginx
# /etc/nginx/sites-available/rlcd
server {
    listen 443 ssl;
    server_name rlcd.example.com;

    ssl_certificate     /etc/letsencrypt/live/rlcd.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/rlcd.example.com/privkey.pem;

    location /api/usage {
        proxy_pass http://127.0.0.1:7777;
        proxy_set_header X-RLCD-Token $http_x_rlcd_token;
    }
    location /healthz {
        proxy_pass http://127.0.0.1:7777;
    }
}
```

```c
// secrets.h — 注意 https://
#define RLCD_BRIDGE_URL   "https://rlcd.example.com/api/usage"
```

> ESP32 HTTP 客户端支持 HTTPS，但需要将服务器 CA 证书内嵌到固件中。使用 Let's Encrypt 证书时，将 ISRG Root X1 的 PEM 内嵌到 `usage_client.c` 并通过 `esp_http_client_config_t.cert_pem` 传入。

### 模式 C — overlay 网络（ZeroTier / Tailscale）

ESP32 需要能访问与 bridge 相同的 overlay 网络——通常通过家用路由器或常开设备（树莓派、NAS）接入 overlay 并路由流量到家庭局域网。

```c
// secrets.h — Tailscale
#define RLCD_BRIDGE_URL   "http://100.x.x.x:7777/api/usage"

// secrets.h — ZeroTier
#define RLCD_BRIDGE_URL   "http://10.x.x.x:7777/api/usage"
```

#### ZeroTier MTU 问题

若 TCP 握手成功但响应始终收不到，原因是 ZeroTier 默认 MTU 2800 字节大于实际路径 MTU（约 1400 字节）。在 VPS 上修复：

```bash
# 先查你的 ZeroTier 接口名：
ip link show | grep zt

sudo scripts/vps-zt-mtu-fix.sh <zt-接口名>
sudo cp scripts/rlcd-zt-fix.service /etc/systemd/system/
sudo systemctl enable --now rlcd-zt-fix.service   # 开机自动生效
```

最简洁的方案是在 ZeroTier Central 把网络 MTU 设为 1400，所有成员自动生效。

---

## 项目结构

```
token-monitor-RLCD/
├── bridge/                    # Python FastAPI bridge 守护进程
│   ├── bridge.py              # 主程序 + 后台刷新缓存
│   ├── schema.py              # Pydantic 响应模型
│   ├── pet_hook.js            # AI agent 事件->动画状态映射（单一真相源）
│   ├── codex_hooks.json       # Codex hooks 配置真相源
│   ├── install_codex_hooks.py # Codex hooks 幂等安装脚本
│   ├── sim.html               # RLCD 网页模拟器（/sim 路径）
│   ├── sources/
│   │   ├── claude_local.py    # ccusage 集成
│   │   ├── codexradar.py      # Codex Radar 基准测试数据源
│   │   ├── deepseek.py        # DeepSeek 余额 API
│   │   └── weather.py         # CMA 优先，Open-Meteo/Caiyun 坐标兜底
│   ├── assets/
│   │   ├── newgif/            # 黑白 GIF 素材（生成脚本优先读取）
│   │   └── clawd_rlcd/        # 缩放版预览素材（size-56 / size-184）
│   └── pyproject.toml
├── firmware/                  # ESP-IDF v5 + LVGL v9 项目
│   ├── main/
│   │   ├── secrets.h.example  # -> 复制为 secrets.h（已 gitignore）
│   │   └── user_config.h      # 引脚定义（来自厂商 BSP）
│   └── components/
│       ├── net_app/           # Wi-Fi STA + NTP（CST-8）
│       ├── sensor/            # SHTC3 温湿度驱动
│       ├── usage_client/      # HTTP 轮询 + cJSON 解析
│       └── ui_app/            # LVGL 双栏仪表盘 + 雷达页 + 宠物动画
├── scripts/
│   ├── gen_pet_anim.py        # 从 GIF 生成 56px 宠物动画帧表
│   ├── gen_pet_big_anim.py    # 从 GIF 生成 184px 大宠物动画帧表
│   ├── gen_icons.py           # 图标精灵表生成
│   ├── convert_clawd_to_rlcd_bw.py  # 彩色 GIF -> 黑白 GIF 转换
│   ├── install-bridge-linux.sh      # systemd --user 安装脚本
│   └── vps-zt-mtu-fix.sh           # ZeroTier MTU/MSS 修复
├── rlcd-pet-zcode/            # ZCode 插件：生命周期事件 -> 动画触发
├── rlcd-pet-opencode/         # OpenCode 插件：同上
├── clawd-on-desk/             # Clawd 螃蟹角色上游参考（本地保留，未提交）
├── docs/
│   ├── TOOLCHAIN.md           # 工具链路径指引
│   ├── mockup.py              # UI mockup 生成脚本
│   └── ui-mockup.txt          # UI mockup 文本稿
├── AGENTS.md                  # AI agent 项目规则入口
├── CONTEXT.md                 # 项目术语表
└── .gitattributes             # 行尾规范化（LF）
```

## 许可证

MIT
