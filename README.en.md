# token-monitor-RLCD

[中文文档](README.md)

A desktop ornament that shows your live Claude, Codex, and DeepSeek usage on a Waveshare ESP32-S3-RLCD-4.2 reflective-LCD board.

## How it works

```
~/.claude/**/*.jsonl   (Claude Code session logs, written locally)
         │
         ▼
   bridge daemon                              ESP32-S3-RLCD-4.2
   ─────────────                              ─────────────────
   • runs ccusage to parse local usage        • connects to WiFi on boot
   • aggregates Claude / Codex day-month-life • polls GET /api/usage every 60 s
                                              • parses JSON with cJSON
   • fetches DeepSeek account balance         • drives LVGL two-column UI:
   • fetches outdoor weather (CMA/Open-Meteo)   carousel → Claude / DeepSeek / Codex
   • receives AI agent lifecycle pet events     top pet + large pet page
   • caches result, serves JSON on :7777
                                              • reads indoor temp/RH (SHTC3)
                                              • shows time via NTP (CST-8)
```

The bridge runs as a systemd `--user` service on the same machine as Claude
Code / Codex. It keeps a background thread that refreshes ccusage every 45 s so the
ESP32's HTTP request always returns instantly from cache (a cold ccusage run
takes ~10 s). Pet state is updated by `bridge/pet_hook.js`, which receives
agent lifecycle events and folds the current animation state into `/api/usage`.

```
14:30                            ☁  24°C
IN 26.3°C  65%RH         SHENZHEN  Partly
──────────────────────────────────────────
 CLAUDE           │ CODEX            │ DEEPSEEK
 opus   12.9M     │ o3     8.2M      │      balance
 sonnet  4.4M     │ gpt-4o 3.1M      │    ¥ 70.79
 ──────────────── │ ──────────────── │ ────────────────
 today 382K $9.14 │ today 1.2M $3.20 │ granted   0.00
 month 8.4M $187  │ month  28M $76   │ topped   70.79
 total 18.2M $214 │ total  52M $142  │ today    2.4M
```

## Pages

Cycle through three pages with the BOOT button:

### Token dashboard (default)

Two-column display of Claude / DeepSeek / Codex today, month, and lifetime usage and cost. Top bar shows weather, time, and indoor temp/humidity. Bottom shows a small pet animation.

### Clawd pet animation

184×184 large Clawd the crab animation showing the current AI agent's working state. The character is derived from the [clawd-on-desk](https://github.com/rullerzhou-afk/clawd-on-desk) project, re-rendered in black and white for the reflective LCD.

### Codex Radar

3×3 grid showing intelligence-efficiency benchmark data from [codexradar.com](https://codexradar.com):

- Rows: sol (sun) / terra (earth) / luna (moon) models
- Columns: ULTRA / MAX / XHIGH effort levels
- Each cell shows IQ, price, time, pass rate (/112), and a trend sparkline
- Auto-refreshes every 10 minutes; top bar shows countdown to next refresh


## Hardware

- [Waveshare ESP32-S3-RLCD-4.2](https://www.waveshare.com/wiki/ESP32-S3-RLCD-4.2) — 4.2" reflective LCD (paper-like), ESP32-S3, WiFi, RTC, temp/humidity, SD, audio.
- USB-C cable for flashing.

## Architecture

```
Linux / macOS PC                          ESP32-S3-RLCD-4.2
────────────────                          ─────────────────
~/.claude/**/*.jsonl                      LVGL pet animation + dashboard
        │                                         ▲
        ▼                      LAN HTTP           │
   bridge daemon ──── GET /api/usage (60s) ──────┘
   (spawns ccusage)
   :7777
```

- **Bridge** (`bridge/`) — Python FastAPI daemon. Spawns `ccusage blocks/daily/monthly --json`, flattens into one schema, serves at `http://<host>:7777/api/usage`. Runs under systemd `--user`. Includes a web simulator at `bridge/sim.html` (served at `/sim`).
- **Firmware** (`firmware/`) — ESP-IDF + LVGL v9. Polls the bridge every 60 s, renders a two-column dashboard on the RLCD with a pet animation (Clawd the crab).

## Pet Animation System

The device shows a Clawd crab character below the dashboard, switching animations based on usage state:

| State | Animation | Trigger |
|-------|-----------|---------|
| idle | reading book | No activity / AI agent idle |
| thinking | thought bubble | AI agent is thinking |
| working | typing at keyboard | AI agent is generating |
| juggling | juggling balls | Agent parallel tasks |
| headphones-groove | headphones dancing | Multi-agent (sessions ≥ 2) |
| building | building blocks | Building/compiling in progress |
| sweeping | sweeping | Cleanup/organizing |
| sleeping | sleep / yawn / doze | Long inactivity |
| notification | notification bell | New notification |
| error | exclamation mark | Error occurred |
| happy / carrying / debugger / conducting / bubble | — | Various events |

Animation frame data is generated from black/white GIF sources by `scripts/gen_pet_anim.py` (56×56) and `scripts/gen_pet_big_anim.py` (184×184), compiled into the firmware as C frame tables.

### Asset Pipeline

```
clawd-on-desk/assets/gif/       ← Upstream color GIFs (do not touch)
       │
       ▼ (re-rendered as black/white)
bridge/assets/newgif/              ← B/W GIFs (scripts read these first)
       │
       ├──→ bridge/assets/clawd_rlcd/size-184/gifs/  ← Big anim preview
       └──→ bridge/assets/clawd_rlcd/size-56/gifs/   ← Small anim preview
       │
       ▼ (rasterize + resize, generate C tables)
scripts/gen_pet_anim.py → firmware/components/ui_app/pet_anim.c
scripts/gen_pet_big_anim.py → firmware/components/ui_app/pet_big_anim.c
```

### ZCode / Claude Code / Codex Integration

AI agent events are forwarded to the bridge via the `rlcd-pet-zcode/` plugin (ZCode) or `bridge/pet_hook.js` (generic). The bridge switches to the matching animation.

- **`rlcd-pet-zcode/`** — ZCode plugin, forwards lifecycle events via `hooks/run-hook.cmd` → `pet_hook.js --agent zcode`
- **`bridge/pet_hook.js`** — Single source of truth for event→state mapping; supports `--agent` flag to distinguish sources
- Multi-agent concurrency (sessions ≥ 2) switches to `headphones-groove` animation automatically

---

## Deployment

### Step 1 — Prerequisites

On the machine where Claude Code runs (Linux):

```bash
# 1. uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Node/npm + global ccusage (the bridge runtime does not call npx/@latest)
# Ubuntu/Debian:
sudo apt install nodejs npm
# or use nvm: https://github.com/nvm-sh/nvm
npm install -g ccusage

# 3. Verify ccusage works
ccusage --help
```

### Step 2 — Clone and test the bridge

```bash
git clone https://github.com/Winter-And-You-Gone/token-monitor-RLCD.git
cd token-monitor-RLCD/bridge

uv sync                            # install Python deps (first time only)
uv run python bridge.py            # starts on :7777
```

In another terminal:

```bash
curl http://localhost:7777/api/usage | jq          # live data
curl 'http://localhost:7777/api/usage?mock=1' | jq # canned mock — no ccusage needed
```

### Step 3 — Install the bridge as a systemd service

```bash
# From repo root:
scripts/install-bridge-linux.sh
```

This creates `~/.config/systemd/user/rlcd-bridge.service`, enables it, and starts it.

```bash
systemctl --user status rlcd-bridge
journalctl --user -u rlcd-bridge -f
```

To keep it running after logout (VPS / headless server):

```bash
loginctl enable-linger $USER
```

#### Optional env vars

Create `bridge/.env` (git-ignored) with any of these:

```ini
RLCD_HOST=127.0.0.1        # bind address; use 0.0.0.0 for LAN access with a token
RLCD_PORT=7777              # bind port    (default 7777)
RLCD_AUTH_TOKEN=<random>   # required when bridge is reachable beyond loopback
RLCD_PET_MOUSE_MONITOR=1    # Windows bridge monitors mouse movement to reset/wake pet idle-sleep timing
RLCD_PET_MOUSE_POLL_SEC=0.5 # mouse position poll interval
RLCD_PET_MOUSE_IDLE_SEC=20   # seconds with no pet events before one idle animation
RLCD_PET_IDLE_LOOK_SEC=14    # idle animation duration; default uses the reading animation
RLCD_PET_MOUSE_SLEEP_SEC=300 # seconds with no pet events before yawning/dozing/sleeping; original Clawd is 60
RLCD_PET_SLEEP_SEQUENCE=1    # set 0 to disable the automatic sleep sequence
RLCD_PET_ACTIVE_TTL_SEC=90   # pet active state TTL (seconds)
RLCD_PET_COMPLETED_HOLD_SEC=2.0 # how long to hold the "completed" state (seconds)
RLCD_PET_YAWN_SEC=3.0        # yawning animation duration (seconds)
RLCD_PET_DEEP_SLEEP_SEC=600  # deep sleep timeout (seconds of no events)
RLCD_PET_COLLAPSE_SEC=0.8    # collapse-to-sleep transition duration (seconds)
RLCD_PET_WAKE_SEC=1.5        # wake-up transition duration (seconds)
RLCD_PET_IDLE_LOOK_ASSET=clawd-idle-reading.svg  # idle fidget animation asset
RLCD_PET_MOUSE_MIN_DELTA=1.0 # minimum mouse move distance to trigger (pixels)
RLCD_PET_MAX_SESSIONS=20     # max Codex/Jupyter sessions
RLCD_CODEX_JSONL_MONITOR=1   # enable Codex JSONL monitoring
RLCD_CODEX_JSONL_POLL_SEC=1.5 # Codex JSONL poll interval
RLCD_CODEX_JSONL_RECENT_SEC=120 # Codex recent session time window (seconds)
RLCD_REFRESH_SEC=45          # ccusage background refresh interval (seconds)
RLCD_INCLUDE_OTHERS=1        # include "others" category in dashboard
RLCD_ALLOW_QUERY_TOKEN=0     # set to 1 to allow ?token= in query string (default: header only)
RLCD_TZ=Asia/Hong_Kong       # day-rollover timezone for "today/month" usage
RLCD_WEATHER_CMA=1           # enable CMA (China Meteorological Admin) for Chinese city names
RLCD_WEATHER_LAT=30.2741     # fallback latitude (default: Hangzhou)
RLCD_WEATHER_LON=120.1551    # fallback longitude
RLCD_WEATHER_CITY=Hangzhou   # city label on device (≤8 chars)
RLCD_WEATHER_OVERRIDE_TTL=600 # weather override data TTL (seconds)
RLCD_WEATHER_OVERRIDE_RETRY_SEC=30 # weather override retry interval
```

Reload after editing:

```bash
systemctl --user restart rlcd-bridge
```

**Always set `RLCD_AUTH_TOKEN`** when the bridge listens on anything beyond loopback. Generate one with:

```bash
openssl rand -hex 32
```

### Step 4 — Build and flash the firmware

#### Prerequisites

- [ESP-IDF v5.x](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/get-started/)
- Windows: download the **Universal Online Installer** from <https://dl.espressif.com/dl/esp-idf/> (pick latest v5.x, target `esp32s3`).

#### Linux / macOS

```bash
cd firmware
cp main/secrets.h.example main/secrets.h
$EDITOR main/secrets.h           # fill in WiFi SSID/pass + bridge URL + token

idf.py set-target esp32s3
idf.py build flash monitor       # Ctrl+] to exit monitor
```

#### Windows (PowerShell via ESP-IDF Start Menu shortcut)

```powershell
cd C:\path\to\token-monitor-RLCD\firmware
copy main\secrets.h.example main\secrets.h
notepad main\secrets.h           # fill in WiFi / bridge URL / token
idf.py set-target esp32s3
idf.py build flash monitor
```

#### `secrets.h` values

| Key | Example | Notes |
|-----|---------|-------|
| `RLCD_WIFI_SSID` | `"MyNetwork"` | 2.4 GHz only (ESP32 does not support 5 GHz) |
| `RLCD_WIFI_PASSWORD` | `"password"` | WPA2 |
| `RLCD_BRIDGE_URL` | `"http://192.168.1.42:7777/api/usage"` | bridge host address — see deployment modes below |
| `RLCD_BRIDGE_TOKEN` | `""` | match `RLCD_AUTH_TOKEN` if set, else leave empty |
| `RLCD_POLL_SEC` | `60` | poll interval in seconds |

The first build downloads `lvgl/lvgl@^9.4.0` via the IDF component manager (~50 MB) — needs internet.

### Step 5 — Verify

1. Serial monitor prints `connecting to <ssid>...` → `got IP ...`, then the dashboard fills.
2. Use mock mode first: set `RLCD_BRIDGE_URL` to `.../api/usage?mock=1`, flash, confirm the UI renders.
3. Switch to live mode, run Claude Code / Codex for a minute, watch `claude.today.tokens_used` or `other[].today.tokens_used` increase on the next poll.
4. Stop the bridge: UI should show `(stale)` but not crash.

---

## Deployment modes

### Mode A — Same LAN (simplest)

Bridge and ESP32 are on the same home/office network.

```ini
# bridge/.env
RLCD_HOST=0.0.0.0
RLCD_AUTH_TOKEN=<random-32-bytes>
```

```c
// secrets.h
#define RLCD_BRIDGE_URL   "http://192.168.1.42:7777/api/usage"
#define RLCD_BRIDGE_TOKEN "<same-token>"
```

### Mode B — Public internet (bridge on a VPS)

Expose the bridge directly on the VPS's public IP. The ESP32 connects over the
open internet, so a strong token and firewall rules are essential.

```ini
# bridge/.env on VPS
RLCD_HOST=0.0.0.0          # or bind to a specific public interface
RLCD_AUTH_TOKEN=<random-32-bytes>
```

```c
// secrets.h
#define RLCD_BRIDGE_URL   "http://203.0.113.10:7777/api/usage"
#define RLCD_BRIDGE_TOKEN "<same-token>"
```

Firewall: open port 7777 only while you need it, or restrict source IP to your
home ISP's address range.

#### Optional: HTTPS via reverse proxy (nginx)

For a more secure setup, put the bridge behind nginx with a TLS certificate
(e.g. from Let's Encrypt via Certbot). This removes the need to open port 7777
and lets you terminate TLS on port 443.

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
// secrets.h — note https://
#define RLCD_BRIDGE_URL   "https://rlcd.example.com/api/usage"
```

> The ESP32 HTTP client supports HTTPS but requires the server CA certificate
> embedded in the firmware. For a Let's Encrypt cert, embed the ISRG Root X1
> PEM in `usage_client.c` and pass it via `esp_http_client_config_t.cert_pem`.

### Mode C — Overlay network (ZeroTier / Tailscale)

The ESP32 must be reachable on the same overlay as the bridge — typically via a
home router or always-on device (Raspberry Pi, NAS) that joins the overlay and
routes traffic to the home LAN.

```c
// secrets.h — Tailscale
#define RLCD_BRIDGE_URL   "http://100.x.x.x:7777/api/usage"

// secrets.h — ZeroTier
#define RLCD_BRIDGE_URL   "http://10.x.x.x:7777/api/usage"
```

#### ZeroTier MTU fix

If the TCP handshake succeeds but responses never arrive, ZeroTier's default
2800-byte MTU is larger than the real path MTU (~1400 bytes). Fix on the VPS:

```bash
# Find your ZeroTier interface name first:
ip link show | grep zt

sudo scripts/vps-zt-mtu-fix.sh <zt-interface>
sudo cp scripts/rlcd-zt-fix.service /etc/systemd/system/
sudo systemctl enable --now rlcd-zt-fix.service   # persists across reboots
```

The cleanest alternative is to set the ZeroTier network MTU to 1400 in ZeroTier Central.

---

## Project layout

```
token-monitor-RLCD/
├── bridge/                    # Python FastAPI bridge daemon
│   ├── bridge.py              # main app + background refresh cache
│   ├── schema.py              # Pydantic response models
│   ├── pet_hook.js            # AI agent event -> animation state mapper (single source of truth)
│   ├── codex_hooks.json       # Codex hooks config source of truth
│   ├── install_codex_hooks.py # Codex hooks idempotent installer
│   ├── sim.html               # RLCD web simulator (/sim route)
│   ├── sources/
│   │   ├── claude_local.py    # ccusage integration
│   │   ├── codexradar.py      # Codex Radar benchmark data source
│   │   ├── deepseek.py        # DeepSeek balance API
│   │   └── weather.py         # CMA first, Open-Meteo/Caiyun fallback
│   ├── assets/
│   │   ├── newgif/            # B/W GIF sources (scripts read these first)
│   │   └── clawd_rlcd/        # Scaled preview assets (size-56 / size-184)
│   └── pyproject.toml
├── firmware/                  # ESP-IDF v5 + LVGL v9 project
│   ├── main/
│   │   ├── secrets.h.example  # -> copy to secrets.h (git-ignored)
│   │   └── user_config.h      # pin assignments (from vendor BSP)
│   └── components/
│       ├── net_app/           # WiFi STA + NTP (CST-8)
│       ├── sensor/            # SHTC3 temp/humidity
│       ├── usage_client/      # HTTP poll + cJSON parse
│       └── ui_app/            # LVGL dashboard + radar page + pet animation
├── scripts/
│   ├── gen_pet_anim.py        # 56px pet animation frame table generator
│   ├── gen_pet_big_anim.py    # 184px big pet animation frame table generator
│   ├── gen_icons.py           # Icon sprite sheet generator
│   ├── convert_clawd_to_rlcd_bw.py  # Color GIF -> B/W GIF converter
│   ├── install-bridge-linux.sh      # systemd --user installer
│   └── vps-zt-mtu-fix.sh           # ZeroTier MTU/MSS fix
├── rlcd-pet-zcode/            # ZCode plugin: lifecycle events -> animation triggers
├── rlcd-pet-opencode/         # OpenCode plugin: same as above
├── clawd-on-desk/             # Clawd crab reference project (local only, not committed)
├── docs/
│   ├── TOOLCHAIN.md           # Toolchain path guide
│   ├── mockup.py              # UI mockup generator script
│   └── ui-mockup.txt          # UI mockup text draft
├── AGENTS.md                  # AI agent project rules entry point
├── CONTEXT.md                 # Project glossary
└── .gitattributes             # Line ending normalization (LF)
```

## License

MIT
