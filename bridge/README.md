# bridge

Python FastAPI daemon that spawns `ccusage` to surface local Claude (and other
LLM-agent) usage data as JSON over HTTP, for the RLCD device to render.

## Prereqs

- Python 3.10+
- [`uv`](https://docs.astral.sh/uv/) (recommended) or any venv tool
- Node/npm, with `ccusage` installed once via `npm install -g ccusage`
- A populated `~/.claude/projects/` (Claude Code has been used on this machine)

## Run

```bash
uv sync                       # one-time
uv run python bridge.py       # serves on 127.0.0.1:7777 by default
curl http://localhost:7777/api/usage | jq
curl 'http://localhost:7777/api/usage?mock=1' | jq   # canned data for firmware bring-up
curl http://localhost:7777/healthz
```

Open `http://localhost:7777/sim` for a browser-based RLCD simulator. It uses
the same `/api/usage` endpoint, supports mock/live mode, token auth, weather
location override, auto refresh, and stale/offline states.

Local supplemental assets and reference projects live under `X:\ESP` on the
development machine. If the simulator or pet animation assets are missing Clawd
SVG/GIF files, vendor demos, or source icons, copy or sync them from there into
the matching repo directory.

## RLCD pet state hook

`pet_hook.js` is a small command hook that posts lifecycle events to
`/api/pet/state`. The bridge folds that state into `/api/usage`, and both the
browser simulator and firmware render the matching Clawd animation.
The event map follows Clawd-on-Desk semantics: tool completions keep the pet in
`working`, `Stop` briefly shows `attention`, and Codex `Stop` resolves to
`attention` only when the turn used tools.

Manual smoke tests:

```bash
node bridge/pet_hook.js UserPromptSubmit
node bridge/pet_hook.js --state working --agent codex --sessions 2
curl http://localhost:7777/api/pet/state
```

Set `RLCD_BRIDGE_URL` when the bridge is not on `http://127.0.0.1:7777`. If the
bridge uses auth, set `RLCD_AUTH_TOKEN` for the hook process as well. The hook
is fail-open by default so a stopped bridge does not interrupt the agent; set
`RLCD_PET_HOOK_STRICT=1` only when debugging hook delivery.

Claude Code hook entries use the event name as the final argument:

```jsonc
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "shell": "powershell",
            "command": "& \"node\" \"X:/ESP32-S3 RLCD/token-monitor-RLCD/bridge/pet_hook.js\" UserPromptSubmit --agent claude-code",
            "async": true,
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

Codex hooks can use the same command under `~/.codex/hooks.json`:

```jsonc
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "& \"node\" \"X:/ESP32-S3 RLCD/token-monitor-RLCD/bridge/pet_hook.js\" UserPromptSubmit --agent codex",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

Antigravity CLI uses `~/.gemini/config/hooks.json`. Keep it state-only and let
Antigravity's native permission UI own approvals:

```jsonc
{
  "rlcd": {
    "PreInvocation": [
      {
        "type": "command",
        "command": "& \"node\" \"X:/ESP32-S3 RLCD/token-monitor-RLCD/bridge/pet_hook.js\" PreInvocation --agent antigravity-cli",
        "timeout": 10
      }
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "& \"node\" \"X:/ESP32-S3 RLCD/token-monitor-RLCD/bridge/pet_hook.js\" PostToolUse --agent antigravity-cli",
            "timeout": 10
          }
        ]
      }
    ],
    "PostInvocation": [
      {
        "type": "command",
        "command": "& \"node\" \"X:/ESP32-S3 RLCD/token-monitor-RLCD/bridge/pet_hook.js\" PostInvocation --agent antigravity-cli",
        "timeout": 10
      }
    ],
    "Stop": [
      {
        "type": "command",
        "command": "& \"node\" \"X:/ESP32-S3 RLCD/token-monitor-RLCD/bridge/pet_hook.js\" Stop --agent antigravity-cli",
        "timeout": 10
      }
    ]
  }
}
```

Keep permission hooks owned by the interactive agent; this RLCD hook is
state-only.

## Endpoints

- `GET /healthz` — liveness + cache age.
- `GET /api/usage` — full usage report from the warm background cache
  refreshed every `RLCD_REFRESH_SEC` seconds (default 45).
- `GET /api/usage?mock=1` — deterministic mock payload, lets you develop firmware
  without poking ccusage.
- `GET /api/usage?weather_city=Tokyo` — look up a city and override the weather
  location for simulator/local testing. Add `weather_lat` and `weather_lon` for
  an exact coordinate override.
- `GET /sim` — interactive browser UI simulator for local testing without the board.

## Response shape

```jsonc
{
  "updated_at": "2026-05-25T03:30:00Z",
  "source": "ccusage",
  "claude": {
    "active_block": {                  // current 5h billing window (Claude Pro/Max + API)
      "started_at": "...", "ends_at": "...",
      "tokens_used": 112006509, "cost_usd": 77.39,
      "tokens_limit": 100000000, "percent_used": 0.42, // from RLCD_TOKEN_LIMIT unless overridden
      "minutes_remaining": 91,
      "projection_tokens": 166000000, "projection_cost_usd": 113.23
    },
    "weekly":   { "tokens_used": ..., "cost_usd": ..., "percent_used": null },
    "today":    { "tokens_used": ..., "cost_usd": ... },
    "month":    { "tokens_used": ..., "cost_usd": ... },
    "lifetime": { "tokens_used": ..., "cost_usd": ... },
    "by_model": [
      { "model": "claude-opus-4-7",    "tokens": ..., "cost_usd": ... },
      { "model": "claude-sonnet-4-6",  "tokens": ..., "cost_usd": ... }
    ]
  },
  "other": [
    { "agent": "codex", "today": {...}, "month": {...}, "lifetime": {...} }
  ]
}
```

## Configuration (env vars)

| Var | Default | Notes |
| --- | --- | --- |
| `RLCD_HOST` | `127.0.0.1` | bind address. Set `0.0.0.0` for LAN access only together with `RLCD_AUTH_TOKEN`; `python bridge.py` refuses non-loopback binds without a token. |
| `RLCD_PORT` | `7777` | bind port |
| `RLCD_REFRESH_SEC` | `45` | background refresh interval. A daemon thread reruns ccusage this often and caches the result; `/api/usage` always returns the cached value instantly (a cold ccusage run takes ~12s, so clients must never block on it) |
| `RLCD_INCLUDE_OTHERS` | `1` | set `0` to skip codex/gemini/copilot probes |
| `RLCD_AUTH_TOKEN` | unset | if set, requests must carry `X-RLCD-Token: <value>`. Required when bridge is reachable from anything beyond loopback. `/healthz` is always open. |
| `RLCD_ALLOW_QUERY_TOKEN` | `0` | set `1` only for legacy clients that still send `?token=<value>`; query tokens can leak into logs. |
| `RLCD_PET_MOUSE_MONITOR` | `1` on Windows, `0` elsewhere | monitors the bridge host mouse cursor and treats movement as Clawd-style activity. This resets idle timing or wakes a sleeping pet; the ESP32 itself does not read mouse input. |
| `RLCD_PET_MOUSE_POLL_SEC` | `0.5` | mouse cursor poll interval in seconds. |
| `RLCD_PET_MOUSE_MIN_DELTA` | `1` | minimum cursor movement in pixels before it counts as activity. |
| `RLCD_PET_MOUSE_IDLE_SEC` | `20` | seconds of no pet events before showing one idle animation. Clawd-on-Desk uses mouse movement for this timer; RLCD has no cursor input. |
| `RLCD_PET_IDLE_LOOK_SEC` | `14` | duration of the idle animation before returning to follow/idle. |
| `RLCD_PET_MOUSE_SLEEP_SEC` | `300` | seconds of no pet events before yawning/dozing/sleeping. Original Clawd's `mouseSleepTimeout` is 60s, but RLCD defaults longer because there is no mouse movement to reset the timer. |
| `RLCD_PET_IDLE_LOOK_ASSET` | `clawd-idle-reading.svg` | idle animation asset used by the bridge. |
| `RLCD_PET_SLEEP_SEQUENCE` | `1` | set `0` to disable the automatic idle-to-sleep sequence. |
| `RLCD_TZ` | `Asia/Hong_Kong` | timezone used for daily/monthly period selection from `ccusage` output. |
| `RLCD_TOKEN_LIMIT` | `100M` | fixed token capacity used for progress bars when real Claude limits are unavailable. Supports `100M`, `1.5B`, or raw numbers. |
| `RLCD_BLOCK_LIMIT_TOKENS` | `RLCD_TOKEN_LIMIT` | optional 5h progress-bar token limit override. |
| `RLCD_WEEKLY_LIMIT_TOKENS` | `RLCD_TOKEN_LIMIT` | optional weekly progress-bar token limit override. |
| `RLCD_WEEKLY_LIMIT_USD` | unset | if set (e.g. `100`), `weekly.percent_used` is computed |
| `RLCD_BLOCK_LIMIT_USD` | unset | same for the 5h window |
| `CCUSAGE_CMD` | unset | optional local command/path override. Runtime `npx` and `@latest` commands are rejected; install once with `npm install -g ccusage` instead. |
| `CCUSAGE_OFFLINE` | `1` | appends `--offline` to ccusage queries so pricing uses embedded data and the bridge does not touch the network/proxy during refresh. |
| `DEEPSEEK_API_KEY` | unset | enables the `deepseek` block (balance from `/user/balance`). Keep it in a 600-perm `EnvironmentFile`, not the unit. |
| `RLCD_WEATHER_CMA` | `1` | use China Meteorological Administration (`weather.cma.cn`) station data first when a city name is available; set `0` to skip it |
| `RLCD_WEATHER_LAT` / `_LON` / `_CITY` | Hangzhou | fallback location for the `weather` block. City names use CMA first, then Open-Meteo; explicit lat/lon uses the coordinate fallback |
| `RLCD_LIMITS_FILE` | `/run/rlcd/claude-limits.json` | where the root limits helper writes real 5h/7d utilization |

### Real Claude 5h/7d utilization

`claude.limits` (the numbers Claude Code's `/usage` shows) come from
`anthropic-ratelimit-unified-*` headers on an authenticated call, which needs
the OAuth token in root's `~/.claude/.credentials.json`. A root systemd timer
runs `scripts/rlcd-claude-limits.py` every 3 min, writes
`/run/rlcd/claude-limits.json`, and this daemon (running as your user) reads it:

```bash
sudo install -m 0755 scripts/rlcd-claude-limits.py /usr/local/sbin/rlcd-claude-limits.py
sudo cp scripts/rlcd-claude-limits.{service,timer} /etc/systemd/system/
sudo systemctl enable --now rlcd-claude-limits.timer
```

Each run costs one 1-token Haiku message (negligible). If the OAuth token
expires and Claude Code isn't running to refresh it, `limits.status` goes
`stale`/`err` and the device keeps showing the last good values.

> Anthropic does **not** publish your Pro/Max plan's 5h or weekly limits via API.
> The bridge uses `RLCD_TOKEN_LIMIT=100M` as a fixed fallback capacity by default.
> Override `RLCD_BLOCK_LIMIT_TOKENS` / `RLCD_WEEKLY_LIMIT_TOKENS`, or set the USD
> budget env vars above if you prefer cost-based bars.

## Install as a systemd `--user` unit

```bash
../scripts/install-bridge-linux.sh
journalctl --user -u rlcd-bridge -f
```

The generated unit reads `bridge/.env`. Put `RLCD_HOST=0.0.0.0` and
`RLCD_AUTH_TOKEN=...` there when the ESP32 needs to reach the bridge from the
LAN; without those values it stays loopback-only.

## Verification

1. `curl :7777/healthz` returns `{"ok": true}`.
2. `curl :7777/api/usage?mock=1` returns the canned shape — useful for offline UI work.
3. `curl :7777/api/usage` shows numbers that match `ccusage blocks --active` /
   `ccusage claude daily` for today.
4. After running a Claude Code session for ~1 min, `active_block.tokens_used`
   in the next response (≤ `RLCD_REFRESH_SEC` seconds later) goes up.
