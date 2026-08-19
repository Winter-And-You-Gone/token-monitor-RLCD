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
            "command": "& \"node\" \"<repo>/bridge/pet_hook.js\" UserPromptSubmit --agent claude-code",
            "async": true,
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

Codex only reads the single global `~/.codex/hooks.json` (no `plugins.dirs`
mechanism like zcode), so `pet_hook.js` entries must live there alongside
other consumers such as Clawd on Desk's `codex-hook.js`. The project-owned
source of truth is `bridge/codex_hooks.json` (pet_hook.js entries only); the
idempotent installer merges it into the global file while preserving every
other hook consumer:

```bash
python bridge/install_codex_hooks.py           # merge (backs up global file)
python bridge/install_codex_hooks.py --check   # dry-run, exit 1 on drift
```

Clawd on Desk periodically rewrites `~/.codex/hooks.json` (see the
`.clawd-cleanup-*.bak` files in `~/.codex/`), which invalidates codex's
`trusted_hash` and silently disables **all** hooks -- the RLCD pet then stops
reacting to codex. When that happens, re-run the installer then **restart
codex** so it re-approves the hooks. The merged entry format:

```jsonc
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "& \"node\" \"<repo>/bridge/pet_hook.js\" UserPromptSubmit --agent codex",
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
        "command": "& \"node\" \"<repo>/bridge/pet_hook.js\" PreInvocation --agent antigravity-cli",
        "timeout": 10
      }
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "& \"node\" \"<repo>/bridge/pet_hook.js\" PostToolUse --agent antigravity-cli",
            "timeout": 10
          }
        ]
      }
    ],
    "PostInvocation": [
      {
        "type": "command",
        "command": "& \"node\" \"<repo>/bridge/pet_hook.js\" PostInvocation --agent antigravity-cli",
        "timeout": 10
      }
    ],
    "Stop": [
      {
        "type": "command",
        "command": "& \"node\" \"<repo>/bridge/pet_hook.js\" Stop --agent antigravity-cli",
        "timeout": 10
      }
    ]
  }
}
```

Keep permission hooks owned by the interactive agent; this RLCD hook is
state-only.

### ZCode (via plugin `rlcd-pet-zcode/`)

ZCode uses the same hook event names as Claude Code (`SessionStart`,
`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `PermissionRequest`)
but discovers hooks through its plugin system, not a `settings.json` hook
config. A ready-made plugin lives at `rlcd-pet-zcode/` in the project root;
its `hooks/run-hook.cmd` forwards each event to `pet_hook.js --agent zcode`
(the single source of truth for the event→state map).

Event → state mapping (decided by `pet_hook.js` `EVENT_TO_STATE`):

| ZCode event         | pet state     |
|---------------------|---------------|
| `SessionStart`      | `idle`        |
| `UserPromptSubmit`  | `thinking`    |
| `PreToolUse`        | `working`     |
| `PostToolUse`       | `working`     |
| `Stop`              | `attention`   |
| `PermissionRequest` | `notification`|

To enable, register the plugin in `~/.zcode/cli/config.json`:

```jsonc
{
  "plugins": {
    "dirs": [
      "X:\\ESP32-S3 RLCD\\token-monitor-RLCD\\rlcd-pet-zcode"
    ],
    "enabledPlugins": {
      "rlcd-pet-zcode@inline": true
    }
  }
}
```

The `@inline` suffix is mandatory — ZCode tags `plugins.dirs` entries with
the `inline` marketplace name. Restart the ZCode session after editing.
See `rlcd-pet-zcode/README.md` for manual smoke-test steps and internals.

### DeepSeek Harness (`dsh`) — dual path

[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (CLI name
`dsh`) is DeepSeek's official coding-agent harness. Like codex, it has **two
independent paths**, both idempotent and coexisting:

1. **JSONL poller (primary, `bridge.py`)** — the bridge tails dsh's durable
   event log at `~/.dsh/sessions/<workspace>/session-<uuid>/session.jsonl.zstd`
   (raw `session.jsonl` also supported) and maps its typed events to pet
   states. Independent of dsh config and plugin state — the reliable path.
   Note: real dsh files are **several concatenated zstd frames** (one per
   durable publish) whose frame headers omit the content size, so the poller
   decodes frame-by-frame; the `zstandard` package is required.
2. **Hooks bridge (backup)** — dsh's `@deepseek-ai/dsh-hooks-claude-code`
   plugin runs this project's `bridge/dsh_hooks.json` (Claude Code dialect)
   on the harness interception points and forwards events to
   `pet_hook.js --agent dsh`. ⚠️ **Hosts without a usable sandbox backend:**
   the harness executes hook commands under the session's sandbox mode; on
   Windows the ACL sandbox needs to grant a workspace write ACE on the
   session working directory, which requires the caller to own the directory
   (owner-implicit WRITE_DAC). On protected directories (e.g. install
   folders owned by `BUILTIN\Administrators`) the grant fails with
   `SetNamedSecurityInfoW failed (Win32 5)` and the hook is recorded as
   `hook/result` decision `pass` but never executes. **Fix (one-time, per
   directory, elevated):** materialize the workspace write ACE in advance —
   derive the SID with `workspaceWriteSid()` (sha256 of the canonical path,
   `packages/sandbox/sandbox-windows-acl`) and add an
   `(OI)(CI)` ACE with mask `0x00110156` (the sandbox's GRANT_MASK) to the
   directory; the sandbox's exact-ACE skip then never touches the DACL
   again. `X:\DeepSeek Harness\deepseek-harness-desktop`, its `DSH Desktop`
   subfolder, and `X:\DSH WorkSpace` were fixed this way on the dev machine.

Event → state mapping (poller, `bridge.py` `DSH_JSONL_EVENT_STATES`):

| dsh session event     | pet state     |
|-----------------------|---------------|
| `session`             | `idle`        |
| `user/message`        | `thinking`    |
| `turn/start`          | `thinking`    |
| `step/start`          | `thinking`    |
| `tool/call`           | `working`     |
| `tool/result`         | `working`     |
| `turn/end`            | see below     |
| `compaction/start`    | `sweeping`    |
| `subagent/descriptor` | `juggling`    |
| `approval/asked`      | `notification`|

`turn/end` carries `data.reason.kind`: `error` → `error`; `interrupted` /
`aborted` → `idle`; `completed` / `max-tokens` → resolved like a codex turn
end (`attention` when the turn used tools, else `idle`). Streaming chunk
events (`reasoning-chunks`, `assistant/chunk`, `tool-call-chunks`, ...) are
deliberately not mapped — the durable `turn/step/tool` events carry the
state transitions.

Hooks backup path events (via `dsh_hooks.json`, Claude Code names):
`SessionStart`→`idle`, `UserPromptSubmit`→`thinking`,
`PreToolUse`/`PostToolUse`→`working`, `PostToolUseFailure`→`error`,
`Stop`→`codex-turn-end`, `SubagentStart`→`juggling`, `SubagentStop`→`working`
(only these 7 events are supported by dsh's hooks bridge; the poller covers
the rest).

Enable the backup path on this machine (desktop profile):

1. Install the plugin into the profile scope (one-time):
   `pnpm --dir ~/.dsh/profiles/desktop install` after adding
   `"@deepseek-ai/dsh-hooks-claude-code": "link:<harness>/packages/hooks/hooks-claude-code"`
   to the profile's `package.json`.
2. Append to `~/.dsh/profiles/desktop/cordis.patch.yml`:
   ```yaml
   - insert:
       - id: dsh-hooks-claude-code
         name: '@deepseek-ai/dsh-hooks-claude-code'
         config:
           configPath: 'X:/ESP32-S3 RLCD/token-monitor-RLCD/bridge/dsh_hooks.json'
   ```
3. Restart the DSH Desktop app. Verify with
   `node <harness>/apps/cli/lib/bin.js --profile desktop --dump-config | grep hooks-claude-code`.
   To uninstall, remove the patch entry and the dependency.

`pet_hook.js` normalizes `dsh`/`deepseek-harness`/`deepseek` to agent `dsh`
and namespaces session ids as `dsh:<uuid>` (a `session-` prefix in hook
payloads is stripped so both paths share one session record). Like codex,
the two maps must be kept in sync: `DSH_JSONL_EVENT_STATES` / `DSH_EVENT_STATES`
(`bridge.py`) ↔ `DSH_EVENT_TO_STATE` (`pet_hook.js`).

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
    "today":    { "tokens_used": ..., "cost_usd": ... },
    "month":    { "tokens_used": ..., "cost_usd": ... },
    "lifetime": { "tokens_used": ..., "cost_usd": ... },
    "by_model": [
      { "model": "claude-opus-4-7",    "tokens": ..., "cost_usd": ... },
      { "model": "claude-sonnet-4-6",  "tokens": ..., "cost_usd": ... }
    ]
  },
  "other": [
    { "agent": "codex", "today": {...}, "month": {...}, "lifetime": {...}, "by_model": [...] }
  ],
  "weather": { "temp_c": 24.3, "condition": "Partly", "icon": "partly", "city": "SHENZHEN" },
  "deepseek": { "balance": 70.79, "currency": "CNY", "today_tokens": 2400000, "available": true },
  "pet": { "state": "working", "agent": "codex", "asset": "clawd-working-typing.svg" }
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
| `RLCD_DSH_JSONL_MONITOR` | `1` | set `0` to disable the dsh session-log poller (`~/.dsh/sessions/**/session.jsonl.zstd`). Requires the `zstandard` package. |
| `RLCD_DSH_JSONL_POLL_SEC` | `1.5` | dsh poll interval in seconds. |
| `RLCD_DSH_JSONL_RECENT_SEC` | `120` | first-seen dsh session files older than this are skipped (no history replay). |
| `RLCD_DSH_SESSION_DIR` | `~/.dsh/sessions` | dsh session-log root override. |
| `RLCD_TZ` | `Asia/Hong_Kong` | timezone used for daily/monthly period selection from `ccusage` output. |
| `CCUSAGE_CMD` | unset | optional local command/path override. Runtime `npx` and `@latest` commands are rejected; install once with `npm install -g ccusage` instead. |
| `CCUSAGE_OFFLINE` | `1` | appends `--offline` to ccusage queries so pricing uses embedded data and the bridge does not touch the network/proxy during refresh. |
| `DEEPSEEK_API_KEY` | unset | enables the `deepseek` block (balance from `/user/balance`). Keep it in a 600-perm `EnvironmentFile`, not the unit. |
| `RLCD_WEATHER_CMA` | `1` | use China Meteorological Administration (`weather.cma.cn`) station data first when a city name is available; set `0` to skip it |
| `RLCD_WEATHER_LAT` / `_LON` / `_CITY` | Hangzhou | fallback location for the `weather` block. City names use CMA first, then Open-Meteo; explicit lat/lon uses the coordinate fallback |

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
3. `curl :7777/api/usage` shows numbers that match `ccusage claude daily` and
   `ccusage codex daily` for today.
4. After running a Claude Code / Codex session for ~1 min, the matching
   `today.tokens_used` value in the next response (≤ `RLCD_REFRESH_SEC`
   seconds later) goes up.
